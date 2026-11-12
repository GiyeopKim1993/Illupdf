/* IlluPDF Client Application Logic - Real Image Processing Pipeline */
(function () {
  'use strict';

  const {
    IlluConvertingEngine,
    IlluGeometry,
    IlluNestingEngine,
    IlluPdfEngine,
  } = window;

  const STATE = {
    imageFile: null,
    imageElement: null,
    rgbaData: null,
    width: 0,
    height: 0,
    prepared: null,
    nestingResult: null,
    pdfStreams: [],
    status: 'idle',
  };

  // UI Elements
  const imageInput = document.getElementById('image-input');
  const zipInput = document.getElementById('zip-input');
  const processBtn = document.getElementById('process-btn');
  const downloadBtn = document.getElementById('download-btn');
  const statusText = document.getElementById('status-text');
  const presetText = document.getElementById('preset-text');
  const logArea = document.getElementById('log-area');
  const canvas = document.getElementById('main-canvas');
  const ctx = canvas.getContext('2d');

  // Logging
  function log(msg) {
    const entry = document.createElement('div');
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logArea.appendChild(entry);
    logArea.scrollTop = logArea.scrollHeight;
  }

  // Clear log
  function clearLog() {
    logArea.innerHTML = '';
  }

  // Step Tracker
  function setStep(stepNum) {
    document.querySelectorAll('.step').forEach((el, idx) => {
      el.className = 'step' + (idx + 1 === stepNum ? ' active' : '');
    });
  }

  // Image File Loading
  imageInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    clearLog();
    log(`이미지 로드 중: ${file.name}`);

    try {
      STATE.imageFile = file;
      STATE.imageElement = await IlluConvertingEngine.loadImageFromFile(file);
      const rgba = IlluConvertingEngine.imageToRGBA(STATE.imageElement);
      STATE.rgbaData = rgba.data;
      STATE.width = rgba.width;
      STATE.height = rgba.height;

      log(`이미지 로드 완료: ${rgba.width}×${rgba.height}`);
      processBtn.disabled = false;
      statusText.textContent = '이미지 준비 완료';
      presetText.textContent = '-';

      // Draw original image preview
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const scale = Math.min(canvas.width / STATE.imageElement.width, canvas.height / STATE.imageElement.height, 1);
      ctx.drawImage(
        STATE.imageElement,
        0, 0, STATE.imageElement.width, STATE.imageElement.height,
        0, 0, STATE.imageElement.width * scale, STATE.imageElement.height * scale
      );

    } catch (err) {
      log(`이미지 로드 오류: ${err.message}`);
      statusText.textContent = '로드 실패';
    }
  });

  // Core Processing
  processBtn.addEventListener('click', async () => {
    if (STATE.status !== 'idle') return;
    if (!STATE.rgbaData) {
      log('이미지를 먼저 선택해 주세요.');
      return;
    }

    STATE.status = 'processing';
    processBtn.disabled = true;
    downloadBtn.disabled = true;
    clearLog();
    log('프로세스 시작...');
    setStep(1);

    try {
      // 1. Converting Engine (Keyring Pipeline)
      statusText.textContent = '변환 중...';
      log('Converting Engine 실행 중');

      const presetSelect = document.getElementById('preset-select');
      const presetName = presetSelect ? presetSelect.value : 'keyring';

      const prepared = IlluConvertingEngine.prepare_artwork_from_rgba({
        rgbaData: STATE.rgbaData,
        width: STATE.width,
        height: STATE.height,
        preset_name: presetName,
        size_mm: null, // PRESETS 기본값 사용 (keyring: 65mm, stand: 148mm, mixed: 40mm)
        observer: (frame) => {
          log(`[${frame.stage}] ${frame.message}`);
          if (frame.theta_rad !== undefined) {
            log(`  → θ=${frame.theta_rad.toFixed(4)} rad, 반복=${frame.iteration}`);
          }
        },
        mask_alpha_threshold: 8,
      });

      STATE.prepared = prepared;
      log(`변환 완료: 프리셋=${prepared.preset_name}, 외곽선=${prepared.outline_outer_paths_pt.length}개`);

      // Draw Converting Preview (outline + keyring hole + boss)
      drawConvertingPreview(prepared);
      setStep(2);

      // 2. Nesting Engine
      statusText.textContent = '네스팅 중...';
      log('Nesting Engine 실행 중');

      const nestingResult = IlluNestingEngine.run_nesting([prepared]);
      STATE.nestingResult = nestingResult;
      presetText.textContent = nestingResult.auto_preset;
      log(`네스팅 완료: 프리셋=${nestingResult.auto_preset}`);

      // Draw Nesting Visualization
      const viz = IlluNestingEngine.generate_nesting_visualization(nestingResult);
      drawNestingPreview(viz);
      setStep(3);

      // 3. PDF Engine
      statusText.textContent = 'PDF 생성 중...';
      log('PDF Engine 실행 중');

      const pdfContent = IlluPdfEngine.build_combined_page_content(
        '/RasterForm', '/OutlineForm'
      );
      log(`PDF 스트림 생성 완료 (${pdfContent.length} 바이트)`);
      STATE.pdfStreams = [pdfContent];

      statusText.textContent = '완료';
      log('모든 프로세스 완료');
      downloadBtn.disabled = false;

    } catch (err) {
      log(`오류 발생: ${err.message}`);
      console.error(err);
      statusText.textContent = '오류';
    } finally {
      STATE.status = 'idle';
      processBtn.disabled = false;
    }
  });

  // Canvas Drawing: Converting result (outline + keyring)
  function drawConvertingPreview(prepared) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const paths = prepared.outline_outer_paths_pt;
    const holes = prepared.outline_inner_paths_pt;
    const balance = prepared.keyring_balance;

    // Scale to fit canvas
    const allPaths = [...paths, ...holes];
    const bounds = IlluGeometry.geometry_bounds({ paths_groups: [allPaths] });
    if (!bounds) return;

    const pathW = bounds[2] - bounds[0];
    const pathH = bounds[3] - bounds[1];
    const scale = Math.min(canvas.width / pathW, canvas.height / pathH, 1) * 0.9;
    const offsetX = (canvas.width - pathW * scale) / 2 - bounds[0] * scale;
    const offsetY = (canvas.height - pathH * scale) / 2 - bounds[1] * scale;

    // Draw outline (green)
    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 2 / scale;
    ctx.fillStyle = 'rgba(76, 175, 80, 0.2)';
    paths.forEach(path => {
      ctx.beginPath();
      ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
      for (let i = 1; i < path.length; i++) {
        ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });

    // Draw keyring hole (pink)
    if (holes.length > 0) {
      ctx.strokeStyle = '#f472b6';
      ctx.lineWidth = 2 / scale;
      ctx.fillStyle = 'rgba(244, 114, 182, 0.3)';
      holes.forEach(path => {
        ctx.beginPath();
        ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
        for (let i = 1; i < path.length; i++) {
          ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      });
    }

    // Draw boss (blue circle)
    if (balance && balance.boss_path_pt) {
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2 / scale;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.2)';
      ctx.beginPath();
      ctx.moveTo(balance.boss_path_pt[0][0] * scale + offsetX, balance.boss_path_pt[0][1] * scale + offsetY);
      for (let i = 1; i < balance.boss_path_pt.length; i++) {
        ctx.lineTo(balance.boss_path_pt[i][0] * scale + offsetX, balance.boss_path_pt[i][1] * scale + offsetY);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // Draw center point
      if (balance.hole_center_pt) {
        ctx.fillStyle = '#38bdf8';
        ctx.beginPath();
        ctx.arc(
          balance.hole_center_pt[0] * scale + offsetX,
          balance.hole_center_pt[1] * scale + offsetY,
          4 / scale, 0, Math.PI * 2
        );
        ctx.fill();
      }
    }
  }

  // Canvas Drawing: Nesting result
  function drawNestingPreview(viz) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!viz || !viz.sheets || viz.sheets.length === 0) return;

    const sheet = viz.sheets[0];
    const sheetW = sheet.size[0];
    const sheetH = sheet.size[1];
    const scale = Math.min(canvas.width / sheetW, canvas.height / sheetH, 1) * 0.9;
    const offsetX = (canvas.width - sheetW * scale) / 2;
    const offsetY = (canvas.height - sheetH * scale) / 2;

    // Draw sheet border
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2 / scale;
    ctx.strokeRect(offsetX, offsetY, sheetW * scale, sheetH * scale);

    // Draw items
    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 2 / scale;
    ctx.fillStyle = 'rgba(76, 175, 80, 0.2)';
    sheet.items.forEach(item => {
      item.paths.forEach(path => {
        ctx.beginPath();
        ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
        for (let i = 1; i < path.length; i++) {
          ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      });
    });

    // Draw pedestals
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth = 2 / scale;
    ctx.fillStyle = 'rgba(251, 191, 36, 0.2)';
    (viz.pedestals || []).forEach(path => {
      ctx.beginPath();
      ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
      for (let i = 1; i < path.length; i++) {
        ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });
  }

  // Download (Simulated)
  downloadBtn.addEventListener('click', () => {
    log('PDF 다운로드 요청 (브라우저 PDF 라이브러리 필요)');
    alert('클라이언트 사이드 PDF 생성이 완료되었습니다. (실제 파일 다운로드는 pdf-lib 등 통합 필요)');
  });

  // Initialize Canvas
  canvas.width = 800;
  canvas.height = 600;
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  log('IlluPDF 클라이언트 초기화 완료');
})();
