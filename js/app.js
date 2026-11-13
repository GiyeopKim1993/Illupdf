/* IlluPDF Client Application Logic
 * 전체 파이프라인: 이미지 → 변환 엔진 → 프리뷰 → 네스팅 → 실제 PDF 파일 생성/다운로드
 * 의존: js/{geometry,keyring-engine,converting-engine,pdf-engine,pdf-writer,nesting-engine}.js
 */
(function () {
  'use strict';

  const {
    IlluConvertingEngine,
    IlluGeometry,
    IlluNestingEngine,
    IlluPdfEngine,
    IlluPdfWriter,
  } = window;

  const STATE = {
    imageFile: null,
    imageElement: null,
    rgbaData: null,
    width: 0,
    height: 0,
    prepared: null,
    nestingResult: null,
    pdfBytes: null,
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
    if (!logArea) return;
    const entry = document.createElement('div');
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logArea.appendChild(entry);
    logArea.scrollTop = logArea.scrollHeight;
  }

  function clearLog() {
    if (logArea) logArea.innerHTML = '';
  }

  function setStatus(text) {
    if (statusText) statusText.textContent = text;
  }

  function setStep(stepNum) {
    document.querySelectorAll('.step').forEach((el, idx) => {
      el.className = 'step' + (idx + 1 === stepNum ? ' active' : '');
    });
  }

  // --- 이미지 로드 ---
  if (imageInput) {
    imageInput.addEventListener('change', async (e) => {
      const file = e.target.files && e.target.files[0];
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
        if (processBtn) processBtn.disabled = false;
        setStatus('이미지 준비 완료');
        if (presetText) presetText.textContent = '-';

        // 원본 프리뷰
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#0f172a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        const scale = Math.min(
          canvas.width / STATE.imageElement.width,
          canvas.height / STATE.imageElement.height,
          1
        );
        const dw = STATE.imageElement.width * scale;
        const dh = STATE.imageElement.height * scale;
        ctx.drawImage(STATE.imageElement, (canvas.width - dw) / 2, (canvas.height - dh) / 2, dw, dh);
      } catch (err) {
        log(`이미지 로드 오류: ${err.message}`);
        setStatus('로드 실패');
      }
    });
  }

  // --- 메인 프로세스 ---
  if (processBtn) {
    processBtn.addEventListener('click', async () => {
      if (STATE.status !== 'idle') return;
      if (!STATE.rgbaData) {
        log('이미지를 먼저 선택해 주세요.');
        return;
      }

      STATE.status = 'processing';
      processBtn.disabled = true;
      if (downloadBtn) downloadBtn.disabled = true;
      clearLog();
      log('프로세스 시작...');
      setStep(1);

      try {
        const presetSelect = document.getElementById('preset-select');
        const presetName = presetSelect ? presetSelect.value : 'keyring';

        // 1) 변환 엔진
        setStatus('변환 중...');
        log(`Converting Engine 실행 (preset=${presetName})`);

        const prepared = IlluConvertingEngine.prepare_artwork_from_rgba({
          rgbaData: STATE.rgbaData,
          width: STATE.width,
          height: STATE.height,
          preset_name: presetName,
          size_mm: null,
          observer: (frame) => {
            if (frame.stage === 'keyring_balance') return; // 반복 로그는 생략
            log(`[${frame.stage}] ${frame.message}`);
          },
          mask_alpha_threshold: 8,
        });

        STATE.prepared = prepared;
        const finalMm = IlluConvertingEngine.final_outline_long_side_mm(prepared);
        log(
          `변환 완료: 외곽선 ${prepared.outline_outer_paths_pt.length}개, ` +
          `구멍 ${prepared.outline_inner_paths_pt.length}개, 최종 ${finalMm.toFixed(2)}mm`
        );

        drawConvertingPreview(prepared);
        setStep(2);

        // 2) 네스팅
        setStatus('네스팅 중...');
        log('Nesting Engine 실행 중');
        const nestingResult = IlluNestingEngine.run_nesting([prepared]);
        STATE.nestingResult = nestingResult;
        if (presetText) presetText.textContent = nestingResult.auto_preset;
        log(`네스팅 완료: 프리셋=${nestingResult.auto_preset}`);

        const viz = IlluNestingEngine.generate_nesting_visualization(nestingResult);
        drawNestingPreview(viz);
        setStep(3);

        // 3) PDF 생성 (실제 파일)
        setStatus('PDF 생성 중...');
        log('PDF 생성 중 (White/Raster/Outline 3레이어 + OCG)');

        const doc = IlluPdfEngine.build_combined_pdf(prepared);
        const bytes = await IlluPdfWriter.assemble_pdf_bytes(doc);
        STATE.pdfBytes = bytes;

        log(`PDF 생성 완료: ${bytes.length.toLocaleString()} 바이트`);
        log(`  페이지: ${(prepared.page_size_pt[0] / 72 * 25.4).toFixed(1)}×${(prepared.page_size_pt[1] / 72 * 25.4).toFixed(1)} mm`);
        log(`  레이어: White / Raster / Outline`);

        setStatus('완료');
        log('모든 프로세스 완료 — PDF 다운로드 가능');
        if (downloadBtn) downloadBtn.disabled = false;
      } catch (err) {
        log(`오류 발생: ${err.message}`);
        console.error(err);
        setStatus('오류');
      } finally {
        STATE.status = 'idle';
        processBtn.disabled = false;
      }
    });
  }

  // --- 변환 결과 프리뷰 ---
  function drawConvertingPreview(prepared) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const paths = prepared.outline_outer_paths_pt;
    const holes = prepared.outline_inner_paths_pt;
    if (!paths.length) return;

    const bounds = IlluGeometry.geometry_bounds({ paths_groups: [paths] });
    const pathW = Math.max(1e-6, bounds[2] - bounds[0]);
    const pathH = Math.max(1e-6, bounds[3] - bounds[1]);
    const scale = Math.min(canvas.width / pathW, canvas.height / pathH) * 0.85;
    const offsetX = (canvas.width - pathW * scale) / 2 - bounds[0] * scale;
    const offsetY = (canvas.height - pathH * scale) / 2 - bounds[1] * scale;

    const tracePath = (path) => {
      ctx.beginPath();
      ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
      for (let i = 1; i < path.length; i++) {
        ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
      }
      ctx.closePath();
    };

    // 컷라인 (빨강, Python colors.py 와 동일)
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 1.5;
    ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
    paths.forEach((path) => {
      tracePath(path);
      ctx.fill();
      ctx.stroke();
    });

    // 구멍
    if (holes.length) {
      ctx.strokeStyle = '#f472b6';
      ctx.lineWidth = 1.5;
      ctx.fillStyle = 'rgba(244, 114, 182, 0.35)';
      holes.forEach((path) => {
        tracePath(path);
        ctx.fill();
        ctx.stroke();
      });
    }

    // 보스(키링)
    const balance = prepared.keyring_balance;
    if (balance && balance.boss_path_pt) {
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.2)';
      tracePath(balance.boss_path_pt);
      ctx.fill();
      ctx.stroke();
      if (balance.hole_center_pt) {
        ctx.fillStyle = '#38bdf8';
        ctx.beginPath();
        ctx.arc(
          balance.hole_center_pt[0] * scale + offsetX,
          balance.hole_center_pt[1] * scale + offsetY,
          3, 0, Math.PI * 2
        );
        ctx.fill();
      }
    }
  }

  // --- 네스팅 프리뷰 ---
  function drawNestingPreview(viz) {
    if (!viz || !viz.sheets || !viz.sheets.length) return;

    const sheet = viz.sheets[0];
    const sheetW = sheet.size[0];
    const sheetH = sheet.size[1];
    const scale = Math.min(canvas.width / sheetW, canvas.height / sheetH) * 0.9;
    const offsetX = (canvas.width - sheetW * scale) / 2;
    const offsetY = (canvas.height - sheetH * scale) / 2;

    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(offsetX, offsetY, sheetW * scale, sheetH * scale);

    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 1.2;
    sheet.items.forEach((item) => {
      item.paths.forEach((path) => {
        ctx.beginPath();
        ctx.moveTo(path[0][0] * scale + offsetX, path[0][1] * scale + offsetY);
        for (let i = 1; i < path.length; i++) {
          ctx.lineTo(path[i][0] * scale + offsetX, path[i][1] * scale + offsetY);
        }
        ctx.closePath();
        ctx.stroke();
      });
    });
  }

  // --- PDF 다운로드 ---
  if (downloadBtn) {
    downloadBtn.addEventListener('click', () => {
      if (!STATE.pdfBytes) {
        log('PDF 가 아직 생성되지 않았습니다.');
        return;
      }
      const preset = STATE.prepared ? STATE.prepared.preset_name : 'output';
      const blob = new Blob([STATE.pdfBytes], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `illupdf_${preset}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      log(`PDF 다운로드 시작: illupdf_${preset}.pdf`);
    });
  }

  // Canvas 초기화
  canvas.width = 800;
  canvas.height = 600;
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  log('IlluPDF 클라이언트 초기화 완료');
})();
