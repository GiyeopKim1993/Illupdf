/*
 * Minimal zero-dependency ZIP archive creator for browser & Node.js.
 * Stores files (STORE mode, compression method 0) since PDFs are already
 * internally FlateDecode-compressed.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.IlluZip = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const CRC_TABLE = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    CRC_TABLE[n] = c >>> 0;
  }

  function crc32(bytes) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
    }
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  function writeU16(view, offset, val) {
    view.setUint16(offset, val & 0xFFFF, true);
  }

  function writeU32(view, offset, val) {
    view.setUint32(offset, val >>> 0, true);
  }

  /**
   * @param {Array<{name: string, data: Uint8Array}>} files
   * @returns {Uint8Array}
   */
  function createZipBytes(files) {
    const encoder = new TextEncoder();
    const entries = files.map((file) => {
      const nameBytes = encoder.encode(file.name);
      const dataBytes = file.data instanceof Uint8Array ? file.data : new Uint8Array(file.data);
      const crc = crc32(dataBytes);
      return { nameBytes, dataBytes, crc, offset: 0 };
    });

    let localSize = 0;
    let centralSize = 0;
    for (const entry of entries) {
      entry.offset = localSize;
      localSize += 30 + entry.nameBytes.length + entry.dataBytes.length;
      centralSize += 46 + entry.nameBytes.length;
    }

    const totalSize = localSize + centralSize + 22;
    const out = new Uint8Array(totalSize);
    const view = new DataView(out.buffer);
    let pos = 0;

    for (const entry of entries) {
      // Local file header signature
      writeU32(view, pos, 0x04034b50);
      writeU16(view, pos + 4, 20); // version needed
      writeU16(view, pos + 6, 0x0800); // UTF-8 flag
      writeU16(view, pos + 8, 0); // store
      writeU16(view, pos + 10, 0); // mod time
      writeU16(view, pos + 12, 0x21); // mod date
      writeU32(view, pos + 14, entry.crc);
      writeU32(view, pos + 18, entry.dataBytes.length);
      writeU32(view, pos + 22, entry.dataBytes.length);
      writeU16(view, pos + 26, entry.nameBytes.length);
      writeU16(view, pos + 28, 0);
      pos += 30;
      out.set(entry.nameBytes, pos);
      pos += entry.nameBytes.length;
      out.set(entry.dataBytes, pos);
      pos += entry.dataBytes.length;
    }

    const centralOffset = pos;
    for (const entry of entries) {
      writeU32(view, pos, 0x02014b50);
      writeU16(view, pos + 4, 20);
      writeU16(view, pos + 6, 20);
      writeU16(view, pos + 8, 0x0800);
      writeU16(view, pos + 10, 0);
      writeU16(view, pos + 12, 0);
      writeU16(view, pos + 14, 0x21);
      writeU32(view, pos + 16, entry.crc);
      writeU32(view, pos + 20, entry.dataBytes.length);
      writeU32(view, pos + 24, entry.dataBytes.length);
      writeU16(view, pos + 28, entry.nameBytes.length);
      writeU16(view, pos + 30, 0);
      writeU16(view, pos + 32, 0);
      writeU16(view, pos + 34, 0);
      writeU16(view, pos + 36, 0);
      writeU32(view, pos + 38, 0);
      writeU32(view, pos + 42, entry.offset);
      pos += 46;
      out.set(entry.nameBytes, pos);
      pos += entry.nameBytes.length;
    }

    // End of central directory record
    writeU32(view, pos, 0x06054b50);
    writeU16(view, pos + 4, 0);
    writeU16(view, pos + 6, 0);
    writeU16(view, pos + 8, entries.length);
    writeU16(view, pos + 10, entries.length);
    writeU32(view, pos + 12, centralSize);
    writeU32(view, pos + 16, centralOffset);
    writeU16(view, pos + 20, 0);

    return out;
  }

  return { createZipBytes, crc32 };
});
