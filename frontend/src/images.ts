export const MAX_IMAGES = 5;
export const MAX_SIDE = 2048;
export const MAX_BYTES = 4_500_000;
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

export interface Attached {
  name: string;
  mediaType: string;
  data: string; // base64, no prefix
  url: string; // data: URL for the preview
  bytes: number;
}

const bytesOf = (dataUrl: string) => Math.ceil(((dataUrl.length - dataUrl.indexOf(",") - 1) * 3) / 4);

const readAsDataUrl = (f: File) =>
  new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error(`Could not read ${f.name || "the image"}`));
    r.readAsDataURL(f);
  });

/** Reads an image file; large pictures are shrunk first (the model looks at ~2000 px at most anyway). */
export async function prepareImage(file: File): Promise<Attached> {
  if (!IMAGE_TYPES.includes(file.type)) throw new Error(`${file.name || "That file"} is not a supported image (PNG, JPEG, GIF or WebP)`);
  let url = await readAsDataUrl(file);
  let mediaType = file.type;
  if (typeof createImageBitmap === "function" && file.type !== "image/gif") {
    try {
      const bmp = await createImageBitmap(file);
      const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
      if (k < 1 || bytesOf(url) > MAX_BYTES) {
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(bmp.width * k));
        c.height = Math.max(1, Math.round(bmp.height * k));
        c.getContext("2d")?.drawImage(bmp, 0, 0, c.width, c.height);
        mediaType = file.type === "image/png" ? "image/png" : "image/jpeg";
        url = c.toDataURL(mediaType, 0.92);
        if (bytesOf(url) > MAX_BYTES) {
          mediaType = "image/jpeg";
          url = c.toDataURL(mediaType, 0.85);
        }
      }
    } catch {
      /* keep the original bytes: the server validates them anyway */
    }
  }
  const bytes = bytesOf(url);
  if (bytes > MAX_BYTES) throw new Error(`${file.name || "The image"} is too large even after shrinking`);
  return { name: file.name || `pasted-image.${mediaType.split("/")[1]}`, mediaType, data: url.slice(url.indexOf(",") + 1), url, bytes };
}

export const sizeLabel = (n: number) => (n < 1024 ? `${n} B` : n < 1_048_576 ? `${Math.round(n / 1024)} KB` : `${(n / 1_048_576).toFixed(1)} MB`);
