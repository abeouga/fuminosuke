// Server limits (backend StorageSettings / ImageProcessingSettings defaults).
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_PIXELS = 30_000_000;
const MAX_EDGE = 8000;
// Large phone photos are reduced to this long edge; 12 MP-class detail is kept.
const TARGET_EDGE = 4800;

/**
 * Make a phone photo acceptable to the server without changing usable images.
 * PNG/JPEG within the server limits are returned unchanged (same bytes). WebP, or a
 * photo over the size / pixel / edge limit (e.g. 48-200 MP camera modes), is re-encoded
 * as JPEG with the camera orientation applied, scaled to fit.
 */
export async function prepareUploadImage(file: File): Promise<File> {
  const type = file.type.toLowerCase();
  const convertible = type === "image/webp" || /\.webp$/i.test(file.name);
  const plain = type === "image/jpeg" || type === "image/png" || /\.(png|jpe?g)$/i.test(file.name);
  if (!convertible && !plain) return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return file;
  }
  try {
    const { width, height } = bitmap;
    const oversized = file.size > MAX_BYTES || width * height > MAX_PIXELS || Math.max(width, height) > MAX_EDGE;
    if (!convertible && !oversized) return file;
    const scale = oversized ? Math.min(1, TARGET_EDGE / Math.max(width, height), Math.sqrt(MAX_PIXELS / (width * height))) : 1;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.imageSmoothingQuality = "high";
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) return file;
    const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg", lastModified: file.lastModified });
  } finally {
    bitmap.close();
  }
}
