// CLIP ViT-B/32 running in the phone's browser (transformers.js + ONNX Runtime WASM).
// Every file is served by the laptop and cached by the service worker, so this works offline on the hill.
const MODEL = 'Xenova/clip-vit-base-patch32';
const MODEL_DIR = `/vendor/models/${MODEL}`;

export const VENDOR_FILES = [
  '/vendor/transformers.min.js',
  '/vendor/ort/ort-wasm-simd-threaded.mjs',
  '/vendor/ort/ort-wasm-simd-threaded.wasm',
  `${MODEL_DIR}/config.json`,
  `${MODEL_DIR}/preprocessor_config.json`,
  `${MODEL_DIR}/onnx/vision_model_quantized.onnx`,
];

let ready;

export function loadClip() {
  ready ??= (async () => {
    // Dynamic import so the rest of the app still works if the model isn't downloaded yet.
    const T = await import('/vendor/transformers.min.js');
    T.env.allowRemoteModels = false;   // never reach for the Hugging Face hub
    T.env.allowLocalModels = true;
    T.env.localModelPath = '/vendor/models/';
    T.env.useBrowserCache = false;     // the service worker already caches /vendor/
    T.env.backends.onnx.wasm.wasmPaths = {
      mjs: '/vendor/ort/ort-wasm-simd-threaded.mjs',
      wasm: '/vendor/ort/ort-wasm-simd-threaded.wasm',
    };
    const [processor, model] = await Promise.all([
      T.AutoImageProcessor.from_pretrained(MODEL),
      T.CLIPVisionModelWithProjection.from_pretrained(MODEL, { dtype: 'q8', device: 'wasm' }),
    ]);
    return { T, processor, model };
  })().catch((e) => { ready = null; throw e; });
  return ready;
}

// Image blob -> L2-normalised 512-d embedding (plain array, so it syncs as JSON).
export async function embed(blob) {
  const { T, processor, model } = await loadClip();
  const image = await T.RawImage.fromBlob(blob);
  const { image_embeds } = await model(await processor(image));
  const v = Array.from(image_embeds.data);
  const norm = Math.hypot(...v) || 1;
  return v.map((x) => +(x / norm).toFixed(6));
}

export function cosine(a, b) {
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot; // both are unit vectors
}
