/*
 * illupdf Client-Side PDF Serializer
 *
 * pdf-engine.js 가 만든 페이지 서술자를 실제 PDF 1.4 바이트로 직렬화한다.
 * 외부 라이브러리 없이 동작하며, Python(pikepdf) 산출물과 동일한 구조를 노린다:
 *   Catalog → Pages → Page
 *   Page /Resources /Properties 에 OCG 3개, /XObject 에 Form 3개 + 직접 이미지
 *   이미지 스트림은 FlateDecode(무압축 폴백), SMask 는 DeviceGray 이미지
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const IlluPdfEngine = require('./pdf-engine.js');
    module.exports = factory(IlluPdfEngine);
  } else {
    root.IlluPdfWriter = factory(root.IlluPdfEngine);
  }
})(typeof self !== 'undefined' ? self : this, function (IlluPdfEngine) {
  'use strict';

  const encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;

  function encodeAscii(str) {
    const out = new Uint8Array(str.length);
    for (let i = 0; i < str.length; i++) out[i] = str.charCodeAt(i) & 0xff;
    return out;
  }

  // --- Deflate: 브라우저는 CompressionStream, Node 는 zlib, 둘 다 없으면 순수 JS ---
  async function deflateRaw(bytes) {
    // 1) 브라우저 표준 API
    if (typeof CompressionStream !== 'undefined' && typeof Response !== 'undefined') {
      try {
        const cs = new CompressionStream('deflate');
        const stream = new Blob([bytes]).stream().pipeThrough(cs);
        const buf = await new Response(stream).arrayBuffer();
        return new Uint8Array(buf);
      } catch (_) { /* fallthrough */ }
    }
    // 2) Node zlib (Bundler/Node 환경)
    if (typeof require === 'function') {
      try {
        const zlib = require('zlib');
        return new Uint8Array(zlib.deflateSync(Buffer.from(bytes)));
      } catch (_) { /* fallthrough */ }
    }
    // 3) 순수 JS 고정 허프만 deflate (LZ77 실제 압축)
    try {
      return zlibDeflateFixed(bytes);
    } catch (_) {
      // 4) 최후 폴백: stored 블록 (항상 유효, 압축률 없음)
      return zlibStore(bytes);
    }
  }

  /**
   * 순수 JS 고정 허프만(fixed Huffman) DEFLATE 인코더 (RFC 1951).
   * LZ77(greedy, window 32KB, max match 258) + 고정 허프만 + zlib 래퍼.
   * 브라우저에 CompressionStream 이 없는 환경을 위한 폴백.
   */
  const LEN_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
  const LEN_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
  const DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
  const DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];

  function zlibDeflateFixed(bytes) {
    const n = bytes.length;
    const out = [];
    let bitBuf = 0, bitCount = 0;

    // LSB-first 비트 패킹
    const putBits = (value, count) => {
      for (let i = 0; i < count; i++) {
        bitBuf |= ((value >>> i) & 1) << bitCount;
        if (++bitCount === 8) { out.push(bitBuf & 0xff); bitBuf = 0; bitCount = 0; }
      }
    };
    // DEFLATE 허프만 코드는 MSB-first → 비트를 뒤집어 LSB-first 스트림에 기록
    const putHuff = (code, len) => {
      let rev = 0;
      for (let i = 0; i < len; i++) rev = (rev << 1) | ((code >>> i) & 1);
      putBits(rev, len);
    };
    // 리터럴/길이 코드 (RFC1951 3.2.6 고정 허프만)
    const putLitLen = (sym) => {
      if (sym < 144) putHuff(0x30 + sym, 8);
      else if (sym < 256) putHuff(0x190 + (sym - 144), 9);
      else if (sym < 280) putHuff(sym - 256, 7);
      else putHuff(0xC0 + (sym - 280), 8);
    };

    // zlib 헤더 (CMF/FLG)
    out.push(0x78, 0x01);
    // 고정 허프만 블록 (BFINAL=1, BTYPE=01)
    putBits(1, 1);
    putBits(1, 2);

    const head = new Int32Array(65536).fill(-1);
    const prev = new Int32Array(n).fill(-1);
    const hash3 = (p) => ((bytes[p] << 16) ^ (bytes[p + 1] << 8) ^ bytes[p + 2]) & 0xffff;

    let i = 0;
    while (i < n) {
      let bestLen = 0, bestDist = 0;
      if (i + 2 < n) {
        const h = hash3(i);
        let j = head[h];
        const maxLen = Math.min(258, n - i);
        let chain = 0;
        while (j >= 0 && i - j <= 32768 && chain < 64) {
          let l = 0;
          while (l < maxLen && bytes[j + l] === bytes[i + l]) l++;
          if (l > bestLen) {
            bestLen = l; bestDist = i - j;
            if (l === maxLen) break;
          }
          j = prev[j];
          chain++;
        }
      }

      if (bestLen >= 3) {
        // 길이 코드
        let lc = LEN_BASE.length - 1;
        while (lc > 0 && LEN_BASE[lc] > bestLen) lc--;
        putLitLen(257 + lc);
        if (LEN_EXTRA[lc]) putBits(bestLen - LEN_BASE[lc], LEN_EXTRA[lc]);
        // 거리 코드 (고정 허프만: 5비트)
        let dc = DIST_BASE.length - 1;
        while (dc > 0 && DIST_BASE[dc] > bestDist) dc--;
        putHuff(dc, 5);
        if (DIST_EXTRA[dc]) putBits(bestDist - DIST_BASE[dc], DIST_EXTRA[dc]);

        for (let k = 0; k < bestLen; k++) {
          const p = i + k;
          if (p + 2 < n) {
            const h2 = hash3(p);
            prev[p] = head[h2];
            head[h2] = p;
          }
        }
        i += bestLen;
      } else {
        putLitLen(bytes[i]);
        if (i + 2 < n) {
          const h2 = hash3(i);
          prev[i] = head[h2];
          head[h2] = i;
        }
        i++;
      }
    }
    putLitLen(256); // end-of-block
    if (bitCount > 0) out.push(bitBuf & 0xff);

    // Adler-32
    let a = 1, c = 0;
    for (let k = 0; k < n; k++) {
      a = (a + bytes[k]) % 65521;
      c = (c + a) % 65521;
    }
    const adler = ((c << 16) | a) >>> 0;
    out.push((adler >>> 24) & 0xff, (adler >>> 16) & 0xff, (adler >>> 8) & 0xff, adler & 0xff);
    return new Uint8Array(out);
  }

  /** 순수 JS zlib(stored) 스트림 생성 — 압축 없이 유효한 zlib 컨테이너만 만든다 */
  function zlibStore(bytes) {
    const MAX = 65535;
    const blocks = Math.max(1, Math.ceil(bytes.length / MAX));
    const out = new Uint8Array(2 + blocks * 5 + bytes.length + 4);
    let p = 0;
    out[p++] = 0x78; // CMF
    out[p++] = 0x01; // FLG (check bits valid, no dict)
    let offset = 0;
    for (let b = 0; b < blocks; b++) {
      const len = Math.min(MAX, bytes.length - offset);
      const isLast = b === blocks - 1;
      out[p++] = isLast ? 0x01 : 0x00;
      out[p++] = len & 0xff;
      out[p++] = (len >> 8) & 0xff;
      out[p++] = (~len) & 0xff;
      out[p++] = ((~len) >> 8) & 0xff;
      out.set(bytes.subarray(offset, offset + len), p);
      p += len;
      offset += len;
    }
    // Adler-32
    let a = 1, c = 0;
    for (let i = 0; i < bytes.length; i++) {
      a = (a + bytes[i]) % 65521;
      c = (c + a) % 65521;
    }
    const adler = ((c << 16) | a) >>> 0;
    out[p++] = (adler >>> 24) & 0xff;
    out[p++] = (adler >>> 16) & 0xff;
    out[p++] = (adler >>> 8) & 0xff;
    out[p++] = adler & 0xff;
    return out.subarray(0, p);
  }

  // --- PDF 이름/문자열 이스케이프 ---
  function escapeName(name) {
    return String(name).replace(/[^!-~]/g, (c) => '#' + c.charCodeAt(0).toString(16).padStart(2, '0'));
  }

  function fmt(value) {
    if (value === null || value === undefined) return 'null';
    if (typeof value === 'number') {
      if (!isFinite(value)) return '0';
      if (Number.isInteger(value)) return String(value);
      return String(Math.round(value * 10000) / 10000);
    }
    return String(value);
  }

  function escapeString(str) {
    return String(str).replace(/([\\()])/g, '\\$1');
  }

  /**
   * 객체를 PDF 문법으로 직렬화한다.
   * 스트림 객체는 { __stream: dict, data } 형태로 표현한다.
   */
  // PDF 에서 이름(name) 객체로 출력해야 하는 속성 값들
  const NAME_KEYWORDS = new Set([
    'XObject', 'Form', 'Image', 'Page', 'Pages', 'Catalog', 'OCG',
    'FlateDecode', 'DeviceRGB', 'DeviceGray', 'DeviceCMYK', 'UseOC', 'UseNone',
    'Separation', 'Page', 'ExtGState',
  ]);

  function serializeValue(value, out) {
    if (value === null || value === undefined) { out.push('null'); return; }
    if (value instanceof OcgRef) { out.push(`${value.id} 0 R`); return; }
    if (value instanceof PdfRef) { out.push(`${value.id} 0 R`); return; }
    if (typeof value === 'number') { out.push(fmt(value)); return; }
    if (typeof value === 'boolean') { out.push(value ? 'true' : 'false'); return; }
    if (typeof value === 'string') {
      if (value.startsWith('/')) { out.push(value); return; }
      out.push(`(${escapeString(value)})`);
      return;
    }
    if (Array.isArray(value)) {
      out.push('[');
      value.forEach((v, i) => { if (i) out.push(' '); serializeValue(v, out); });
      out.push(']');
      return;
    }
    if (typeof value === 'object') {
      // Separation colorspace
      if (value.type === 'Separation') {
        out.push('[/Separation /' + escapeName(value.name) + ' /DeviceCMYK ');
        serializeValue(value.tintTransform, out);
        out.push(']');
        return;
      }
      // OCG 참조는 위에서 처리
      out.push('<<');
      let first = true;
      for (const key of Object.keys(value)) {
        if (value[key] === undefined || value[key] === null) continue;
        if (key === 'stream' || key === 'data' || key === '__streamDict') continue;
        const pdfKey = key.startsWith('/') ? key : '/' + escapeName(key);
        if (!first) out.push(' ');
        first = false;
        out.push(pdfKey + ' ');
        const raw = value[key];
        if (typeof raw === 'string' && !raw.startsWith('/') && NAME_KEYWORDS.has(raw)) {
          out.push('/' + raw);
        } else {
          serializeValue(raw, out);
        }
      }
      out.push('>>');
      return;
    }
    out.push('null');
  }

  class PdfRef { constructor(id) { this.id = id; } }
  class OcgRef extends PdfRef {}

  /**
   * 문서 서술자를 PDF 바이트로 만든다.
   * @returns {Promise<Uint8Array>}
   */
  async function assemble_pdf_bytes(doc) {
    const objects = [];           // { id, dict, data|null }
    const pagesId = 1;
    const pageId = 2;
    const contentId = 3;
    let nextId = 4;

    // OCG 객체들
    const ocgRefs = {};
    if (doc.oc_properties && doc.oc_properties.OCGs) {
      for (const ocg of doc.oc_properties.OCGs) {
        const id = nextId++;
        ocgRefs[ocg.Name] = new OcgRef(id);
        objects.push({ id, dict: { Type: '/OCG', Name: ocg.Name } });
      }
    }

    // 리소스 트리 + 이미지/폼 객체 등록
    const xobjectRefs = {};
    const propertiesRefs = {};
    const pending = [];

    function registerNode(node) {
      const id = nextId++;
      pending.push({ node, id });
      return new PdfRef(id);
    }

    function registerResources(resources) {
      const out = {};
      if (resources.XObject) {
        out.XObject = {};
        for (const [name, node] of Object.entries(resources.XObject)) {
          out.XObject[name] = registerNode(node);
        }
      }
      if (resources.Properties) {
        out.Properties = {};
        for (const [name, ocg] of Object.entries(resources.Properties)) {
          out.Properties[name] = ocgRefs[ocg.Name] || ocgRefs[Object.keys(ocgRefs)[0]];
        }
      }
      return out;
    }

    const serializedResources = registerResources(doc.page_resources || {});

    // 이미지/폼 객체 직렬화 (재귀적)
    async function materialize(entry) {
      const { node, id } = entry;
      if (node.Subtype === 'Image' && node.data) {
        const dict = {};
        for (const k of Object.keys(node)) {
          if (k === 'data') continue;
          dict[k] = node[k];
        }
        // SMask 먼저 처리
        if (dict.SMask && dict.SMask.data) {
          const smask = dict.SMask;
          const smaskDict = {};
          for (const k of Object.keys(smask)) {
            if (k === 'data') continue;
            smaskDict[k] = smask[k];
          }
          let smaskBytes = smask.data;
          const compressed = await deflateRaw(smaskBytes);
          if (compressed) { smaskBytes = compressed; smaskDict.Filter = '/FlateDecode'; }
          smaskDict.Length = smaskBytes.length;
          const smaskId = nextId++;
          objects.push({ id: smaskId, dict: smaskDict, data: smaskBytes });
          dict.SMask = new PdfRef(smaskId);
        }
        let bytes = node.data;
        const compressed = await deflateRaw(bytes);
        if (compressed) { bytes = compressed; dict.Filter = '/FlateDecode'; }
        dict.Length = bytes.length;
        objects.push({ id, dict, data: bytes });
        return;
      }
      if (node.Subtype === 'Form') {
        const dict = {
          Type: '/XObject',
          Subtype: '/Form',
          BBox: node.BBox,
          Resources: registerResources(node.Resources || {}),
        };
        if (node.OC) dict.OC = ocgRefs[node.OC.Name];
        let formBytes = encodeAscii(node.stream || '');
        const formCompressed = await deflateRaw(formBytes);
        if (formCompressed) { formBytes = formCompressed; dict.Filter = '/FlateDecode'; }
        dict.Length = formBytes.length;
        objects.push({ id, dict, data: formBytes });
        return;
      }
      // 일반 딕셔너리
      objects.push({ id, dict: node, data: null });
    }

    // 등록 큐 처리 (새 객체가 추가될 수 있으므로 인덱스 루프)
    for (let i = 0; i < pending.length; i++) {
      await materialize(pending[i]);
    }

    // 페이지 콘텐츠
    const contentBytes = encodeAscii(doc.page_content || '');
    const compressedContent = await deflateRaw(contentBytes);
    let finalContent = contentBytes;
    const contentDict = {};
    if (compressedContent) { finalContent = compressedContent; contentDict.Filter = '/FlateDecode'; }
    contentDict.Length = finalContent.length;

    const pageSize = doc.page_size_pt || [595, 842];
    const pageDict = {
      Type: '/Page',
      Parent: new PdfRef(pagesId),
      MediaBox: [0, 0, pageSize[0], pageSize[1]],
      Resources: serializedResources,
      Contents: new PdfRef(contentId),
    };

    const pagesDict = {
      Type: '/Pages',
      Kids: [new PdfRef(pageId)],
      Count: 1,
    };

    const catalog = {
      Type: '/Catalog',
      Pages: new PdfRef(pagesId),
    };
    if (doc.page_mode && doc.page_mode !== 'UseNone') catalog.PageMode = '/' + doc.page_mode;
    if (doc.oc_properties) {
      catalog.OCProperties = {
        OCGs: new PdfRef(pagesId + 10000) // placeholder, 아래에서 교체
      };
    }

    // OCGProperties 는 별도 객체로 (OCGs 배열 + D 딕셔너리)
    let ocgPropsId = null;
    if (doc.oc_properties) {
      ocgPropsId = nextId++;
      const ocgArray = doc.oc_properties.OCGs.map((o) => ocgRefs[o.Name]);
      const d = doc.oc_properties.D;
      // D.Order 와 D.ON 에는 OCG 참조를 넣는다
      objects.push({
        id: ocgPropsId,
        dict: {
          OCGs: ocgArray,
          D: {
            Order: d.Order.map((o) => ocgRefs[o.Name]),
            ON: d.ON.map((o) => ocgRefs[o.Name]),
            Off: [],
          },
        },
        data: null,
      });
      catalog.OCProperties = new PdfRef(ocgPropsId);
    }

    objects.push({ id: pagesId, dict: pagesDict, data: null });
    objects.push({ id: pageId, dict: pageDict, data: null });
    objects.push({ id: contentId, dict: contentDict, data: finalContent });

    // --- 파일 조립 ---
    const chunks = [];
    let offset = 0;
    const push = (bytesOrStr) => {
      const bytes = typeof bytesOrStr === 'string' ? encodeAscii(bytesOrStr) : bytesOrStr;
      chunks.push(bytes);
      offset += bytes.length;
    };

    push('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');

    const sorted = objects.slice().sort((a, b) => a.id - b.id);
    const offsets = new Map();
    const maxId = sorted.length ? sorted[sorted.length - 1].id : 3;

    // 카탈로그(최상위) 객체 id
    const catalogId = nextId++;

    for (const obj of sorted) {
      offsets.set(obj.id, offset);
      push(`${obj.id} 0 obj\n`);
      const parts = [];
      serializeValue(obj.dict, parts);
      push(parts.join(''));
      if (obj.data !== null && obj.data !== undefined) {
        push('\nstream\n');
        push(obj.data);
        push('\nendstream');
      }
      push('\nendobj\n');
    }

    // 카탈로그
    offsets.set(catalogId, offset);
    push(`${catalogId} 0 obj\n`);
    const catParts = [];
    serializeValue(catalog, catParts);
    push(catParts.join(''));
    push('\nendobj\n');

    const xrefOffset = offset;
    const total = Math.max(maxId, catalogId) + 1;
    push(`xref\n0 ${total}\n`);
    push('0000000000 65535 f \n');
    for (let id = 1; id < total; id++) {
      const off = offsets.get(id);
      if (off === undefined) push('0000000000 65535 f \n');
      else push(`${String(off).padStart(10, '0')} 00000 n \n`);
    }
    push(`trailer\n<< /Size ${total} /Root ${catalogId} 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`);

    // 병합
    let totalLen = 0;
    for (const c of chunks) totalLen += c.length;
    const out = new Uint8Array(totalLen);
    let p = 0;
    for (const c of chunks) { out.set(c, p); p += c.length; }
    return out;
  }

  return {
    PdfRef,
    OcgRef,
    assemble_pdf_bytes,
    deflateRaw,
    zlibStore,
    zlibDeflateFixed,
    serializeValue,
    fmt,
  };
});
