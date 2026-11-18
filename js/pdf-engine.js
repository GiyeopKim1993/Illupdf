/*
 * illupdf Client-Side PDF Engine
 * 포팅 대상: src/illupdf/converting/engine/pdf/{colors,content,renderer}.py
 *
 * Python 원본과 동일한 구조를 만든다:
 *   - OCG 3개: White / Raster / Outline
 *   - Form XObject: /RasterForm, /OutlineForm, /WhiteForm
 *   - 페이지 리소스 직접 이미지: /ImWhite (Separation[/Spot_0] + SMask), /ImWhiteSpot1 (Separation[/Spot_1] + SMask)
 *   - 페이지 콘텐츠: /OC /MC0..2 BDC ... EMC 블록 3개
 *     (White → q, cm, spot1, ImWhite Do, Q / RasterForm Do / OutlineForm Do)
 *
 * 빌더는 순수 데이터(딕셔너리/스트림 서술자)를 반환하고,
 * 실제 직렬화는 assemble_pdf_bytes() 가 담당한다.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const IlluGeometry = require('./geometry.js');
    const IlluConvertingEngine = require('./converting-engine.js');
    module.exports = factory(IlluGeometry, IlluConvertingEngine);
  } else {
    root.IlluPdfEngine = factory(root.IlluGeometry, root.IlluConvertingEngine);
  }
})(typeof self !== 'undefined' ? self : this, function (IlluGeometry, IlluConvertingEngine) {
  'use strict';

  const { PreparedArtwork, SpotColorDefinition } = IlluConvertingEngine;

  // --- colors.py ---
  const WHITE_SPOT_CHOICES = {
    spot_0: { name: 'Spot_0', cmyk: [0.0, 0.0, 0.0, 1.0] },
    spot_1: { name: 'Spot_1', cmyk: [0.0, 0.0, 0.0, 1.0] },
    white: { name: 'White', cmyk: [0.0, 0.0, 0.0, 1.0] },
    rdg_white: { name: 'RDG_WHITE', cmyk: [0.0, 0.0, 0.0, 1.0] },
    sswhite: { name: 'SSWhite', cmyk: [0.0, 0.0, 0.0, 1.0] },
  };
  const DEFAULT_WHITE_SPOT = 'spot_0';
  // Python colors.py: OUTLINE_STROKE_RGB = (1.0, 0.0, 0.0), OUTLINE_STROKE_PT = 0.01
  const OUTLINE_STROKE_RGB = [1.0, 0.0, 0.0];
  const OUTLINE_STROKE_PT = 0.01;

  function resolve_white_spot(name) {
    const key = String(name).toLowerCase();
    const spot = WHITE_SPOT_CHOICES[key];
    if (!spot) {
      throw new Error(`white spot must be one of: ${Object.keys(WHITE_SPOT_CHOICES).sort().join(', ')}`);
    }
    return spot;
  }

  // --- content.py ---
  function pdf_name_token(name) {
    const str = typeof name === 'string' ? name : String(name);
    return str.startsWith('/') ? str : `/${str}`;
  }

  function format_number(value) {
    if (value === Math.trunc(value)) return String(Math.trunc(value));
    return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  }

  function paths_to_pdf_commands(paths_pt, paint_operator, compound = false) {
    const all_commands = [];
    for (const path of paths_pt || []) {
      if (!path || !path.length) continue;
      const commands = [`${format_number(path[0][0])} ${format_number(path[0][1])} m`];
      for (let i = 1; i < path.length; i++) {
        commands.push(`${format_number(path[i][0])} ${format_number(path[i][1])} l`);
      }
      commands.push('h');
      all_commands.push(commands.join(' '));
    }
    if (!all_commands.length) return '';
    if (compound) return all_commands.join(' ') + `\n${paint_operator}`;
    return all_commands.map((cmd) => `${cmd}\n${paint_operator}`).join('\n');
  }

  function build_combined_page_content(raster_form_name, outline_form_name) {
    return ['q', `${pdf_name_token(raster_form_name)} Do`, `${pdf_name_token(outline_form_name)} Do`, 'Q'].join('\n') + '\n';
  }

  function build_raster_layer_content(prepared, image_name) {
    const [raster_width_pt, raster_height_pt] = prepared.raster_size_pt;
    const [raster_origin_x, raster_origin_y] = prepared.raster_origin_pt;
    return (
      'q\n' +
      `${format_number(raster_width_pt)} 0 0 ${format_number(raster_height_pt)} ` +
      `${format_number(raster_origin_x)} ${format_number(raster_origin_y)} cm\n` +
      `${pdf_name_token(image_name)} Do\n` +
      'Q\n'
    );
  }

  function build_outline_layer_content(prepared) {
    return (
      'q\n' +
      `${OUTLINE_STROKE_RGB[0].toFixed(0)} ${OUTLINE_STROKE_RGB[1].toFixed(0)} ${OUTLINE_STROKE_RGB[2].toFixed(0)} RG\n` +
      `${format_number(OUTLINE_STROKE_PT)} w\n` +
      '1 J\n' +
      '1 j\n' +
      `${paths_to_pdf_commands(prepared.outline_paths_pt, 'S')}\n` +
      'Q\n'
    );
  }

  // --- renderer.py ---
  function make_ocg(name) {
    return { Type: 'OCG', Name: name };
  }

  function assign_oc_properties(ocgs) {
    return { OCGs: ocgs.slice(), D: { Order: ocgs.slice(), ON: ocgs.slice(), Off: [] } };
  }

  function make_form_xobject(page_size_pt, resources, content, layer_ocg = null) {
    const dict = {
      Type: 'XObject',
      Subtype: 'Form',
      BBox: [0.0, 0.0, page_size_pt[0], page_size_pt[1]],
      Resources: resources,
      stream: content,
    };
    if (layer_ocg !== null && layer_ocg !== undefined) dict.OC = layer_ocg;
    return dict;
  }

  /** content.py / white.py: Separation colorspace (tint transform FunctionType 2) */
  function make_white_separation_colorspace(white_spot) {
    return {
      type: 'Separation',
      name: white_spot.name,
      alternate: 'DeviceCMYK',
      tintTransform: {
        FunctionType: 2,
        Domain: [0, 1],
        Range: [0, 1, 0, 1, 0, 1, 0, 1],
        C0: [0, 0, 0, 0],
        C1: white_spot.cmyk.slice(),
        N: 1,
      },
    };
  }

  /** 8-bit gray 이미지 스트림 서술자 (raw bytes 는 직렬화 시 Flate 또는 raw) */
  function make_gray_image(width, height, data, extra = {}) {
    return {
      Type: 'XObject',
      Subtype: 'Image',
      Width: width,
      Height: height,
      ColorSpace: 'DeviceGray',
      BitsPerComponent: 8,
      data,
      ...extra,
    };
  }

  /** renderer.py make_raster_image_xobject: RGB 이미지 + 알파 SMask */
  function make_raster_image_xobject(rgba) {
    const { data, width, height } = rgba;
    const n = width * height;
    const rgb = new Uint8Array(n * 3);
    const alpha = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      rgb[i * 3] = data[i * 4];
      rgb[i * 3 + 1] = data[i * 4 + 1];
      rgb[i * 3 + 2] = data[i * 4 + 2];
      alpha[i] = data[i * 4 + 3];
    }
    const smask = make_gray_image(width, height, alpha);
    return {
      Type: 'XObject',
      Subtype: 'Image',
      Width: width,
      Height: height,
      ColorSpace: 'DeviceRGB',
      BitsPerComponent: 8,
      SMask: smask,
      data: rgb,
    };
  }

  /**
   * renderer.py build_combined_pdf 과 동일한 구조의 페이지 서술자를 만든다.
   * 반환값은 직렬화 가능한 순수 JS 객체.
   */
  function build_combined_pdf(prepared, options = {}) {
    const whiteSpotName = options.white_spot || DEFAULT_WHITE_SPOT;
    const white_spot = resolve_white_spot(whiteSpotName);

    // 1. OCG 3개
    const raster_ocg = make_ocg('Raster');
    const outline_ocg = make_ocg('Outline');
    const white_ocg = make_ocg('White');
    const oc_properties = assign_oc_properties([raster_ocg, outline_ocg, white_ocg]);

    // 2. Raster Form
    const rasterImage = make_raster_image_xobject(prepared.rgba_image);
    const raster_resources = { XObject: { '/ImRaster': rasterImage } };
    const rasterContent = build_raster_layer_content(prepared, '/ImRaster');
    const raster_form = make_form_xobject(prepared.page_size_pt, raster_resources, rasterContent, raster_ocg);

    // 3. Outline Form
    const outlineContent = build_outline_layer_content(prepared);
    const outline_form = make_form_xobject(prepared.page_size_pt, { XObject: {} }, outlineContent, outline_ocg);

    // 4. White Form + direct images
    const white_colorspace = make_white_separation_colorspace(white_spot);
    const whiteData = prepared.white_raster_img;
    const white_coverage = prepared.white_coverage_mask;

    const white_img_direct = {
      Type: 'XObject',
      Subtype: 'Image',
      Width: whiteData.width,
      Height: whiteData.height,
      ColorSpace: white_colorspace,
      BitsPerComponent: 8,
      data: whiteData.data,
      SMask: white_coverage ? make_gray_image(white_coverage.width, white_coverage.height, white_coverage.data) : null,
    };

    let spot1_img_direct = null;
    if (prepared.white_spot1_img) {
      const spot1 = { name: 'Spot_1', cmyk: [0.0, 0.0, 0.0, 1.0] };
      const spot1_colorspace = make_white_separation_colorspace(spot1);
      spot1_img_direct = {
        Type: 'XObject',
        Subtype: 'Image',
        Width: prepared.white_spot1_img.width,
        Height: prepared.white_spot1_img.height,
        ColorSpace: spot1_colorspace,
        BitsPerComponent: 8,
        data: prepared.white_spot1_img.data,
        SMask: white_coverage ? make_gray_image(white_coverage.width, white_coverage.height, white_coverage.data) : null,
      };
    }

    // White Form (래스터 이미지 1개 + 선택적 Spot1)
    const whiteFormResources = {
      XObject: {
        '/ImWhite': white_img_direct,
        ...(spot1_img_direct ? { '/ImWhiteSpot1': spot1_img_direct } : {}),
      },
    };
    const [rw, rh] = prepared.raster_size_pt;
    const [rox, roy] = prepared.raster_origin_pt;
    const white_form_content =
      'q\n' +
      `${format_number(rw)} 0 0 ${format_number(rh)} ${format_number(rox)} ${format_number(roy)} cm\n` +
      (spot1_img_direct ? '/ImWhiteSpot1 Do\n' : '') +
      '/ImWhite Do\n' +
      'Q\n';
    const white_form = make_form_xobject(prepared.page_size_pt, whiteFormResources, white_form_content, null);

    // 5. 페이지 콘텐츠 (Python build_combined_pdf 와 동일한 순서)
    const clip_paths = (prepared.outline_outer_paths_pt && prepared.outline_outer_paths_pt.length)
      ? prepared.outline_outer_paths_pt
      : prepared.base_paths_pt;
    const clip_cmds = clip_paths && clip_paths.length ? paths_to_pdf_commands(clip_paths, 'W n') : '';
    const white_clip_prefix = clip_cmds ? `${clip_cmds}\n` : '';
    const spot1_draw = spot1_img_direct ? '/ImWhiteSpot1 Do\n' : '';
    const page_content =
      '/OC /MC0 BDC\n' +
      'q\n' +
      white_clip_prefix +
      `${format_number(rw)} 0 0 ${format_number(rh)} ${format_number(rox)} ${format_number(roy)} cm\n` +
      spot1_draw +
      '/ImWhite Do\n' +
      'Q\n' +
      'EMC\n' +
      '/OC /MC1 BDC\n' +
      'q\n' +
      '/RasterForm Do\n' +
      'Q\n' +
      'EMC\n' +
      '/OC /MC2 BDC\n' +
      'q\n' +
      '/OutlineForm Do\n' +
      'Q\n' +
      'EMC\n';

    const page_resources = {
      XObject: {
        '/RasterForm': raster_form,
        '/OutlineForm': outline_form,
        '/WhiteForm': white_form,
        '/ImWhite': white_img_direct,
        ...(spot1_img_direct ? { '/ImWhiteSpot1': spot1_img_direct } : {}),
      },
      Properties: { '/MC0': white_ocg, '/MC1': raster_ocg, '/MC2': outline_ocg },
    };

    return {
      type: 'PDFDocument',
      page_size_pt: prepared.page_size_pt,
      page_mode: 'UseOC',
      oc_properties,
      page_resources,
      page_content,
      layers: { White: white_form, Raster: raster_form, Outline: outline_form },
    };
  }

  /**
   * renderer.py build_split_layer_pdf 과 동일한 레이어별 단일 페이지 PDF 서술자.
   */
  function build_split_layer_pdf(prepared, layer_name) {
    if (layer_name === 'Raster') {
      const img = make_raster_image_xobject(prepared.rgba_image);
      return {
        type: 'PDFDocument',
        page_size_pt: prepared.page_size_pt,
        page_mode: 'UseNone',
        page_resources: { XObject: { '/ImRaster': img } },
        page_content: build_raster_layer_content(prepared, '/ImRaster'),
      };
    }
    if (layer_name === 'Outline') {
      return {
        type: 'PDFDocument',
        page_size_pt: prepared.page_size_pt,
        page_mode: 'UseNone',
        page_resources: { XObject: {} },
        page_content: build_outline_layer_content(prepared),
      };
    }
    if (layer_name === 'White') {
      const white_spot = resolve_white_spot(DEFAULT_WHITE_SPOT);
      const white_colorspace = make_white_separation_colorspace(white_spot);
      const white_coverage = prepared.white_coverage_mask;
      const img = {
        Type: 'XObject',
        Subtype: 'Image',
        Width: prepared.white_raster_img.width,
        Height: prepared.white_raster_img.height,
        ColorSpace: white_colorspace,
        BitsPerComponent: 8,
        data: prepared.white_raster_img.data,
        SMask: white_coverage ? make_gray_image(white_coverage.width, white_coverage.height, white_coverage.data) : null,
      };
      let spot1 = null;
      if (prepared.white_spot1_img) {
        const spot1_def = { name: 'Spot_1', cmyk: [0.0, 0.0, 0.0, 1.0] };
        spot1 = {
          Type: 'XObject',
          Subtype: 'Image',
          Width: prepared.white_spot1_img.width,
          Height: prepared.white_spot1_img.height,
          ColorSpace: make_white_separation_colorspace(spot1_def),
          BitsPerComponent: 8,
          data: prepared.white_spot1_img.data,
          SMask: white_coverage ? make_gray_image(white_coverage.width, white_coverage.height, white_coverage.data) : null,
        };
      }
      const [rw, rh] = prepared.raster_size_pt;
      const [rox, roy] = prepared.raster_origin_pt;
      const content =
        '/OC /MC0 BDC\n' +
        'q\n' +
        `${format_number(rw)} 0 0 ${format_number(rh)} ${format_number(rox)} ${format_number(roy)} cm\n` +
        (spot1 ? '/ImWhiteSpot1 Do\n' : '') +
        '/ImWhite Do\n' +
        'Q\n' +
        'EMC\n';
      const xobjects = { '/ImWhite': img };
      if (spot1) xobjects['/ImWhiteSpot1'] = spot1;
      return {
        type: 'PDFDocument',
        page_size_pt: prepared.page_size_pt,
        page_mode: 'UseOC',
        page_resources: { XObject: xobjects },
        page_content: content,
      };
    }
    throw new Error(`Unknown layer name: ${layer_name}`);
  }

  return {
    PreparedArtwork,
    SpotColorDefinition,
    WHITE_SPOT_CHOICES,
    DEFAULT_WHITE_SPOT,
    OUTLINE_STROKE_RGB,
    OUTLINE_STROKE_PT,
    resolve_white_spot,
    pdf_name_token,
    format_number,
    paths_to_pdf_commands,
    build_combined_page_content,
    build_raster_layer_content,
    build_outline_layer_content,
    make_ocg,
    assign_oc_properties,
    make_form_xobject,
    make_white_separation_colorspace,
    make_gray_image,
    make_raster_image_xobject,
    build_combined_pdf,
    build_split_layer_pdf,
  };
});
