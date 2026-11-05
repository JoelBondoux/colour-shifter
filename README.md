# Colour Shifter

Recolour a batch of product photos using a colour sampled from a reference image. Colour Shifter is a Windows desktop app that uses local AI to find the foreground product. It transfers the sampled colour while retaining the shading, highlights and texture visible in the original photos.

## Start on Windows

1. Install 64-bit [Python 3.11](https://www.python.org/downloads/windows/) with the Python launcher and Tkinter. Use Windows 10 or 11 with an up-to-date DirectX 12 graphics driver for GPU processing.
2. Download or clone this repository, then double-click **Setup.cmd**. This installs the app's dependencies and downloads and verifies the 192 MB model.
3. Double-click **Start.cmd**. From this point on, reference photos, batch inputs and exports stay on the computer. Processing works without an internet connection.

The app defaults to **GPU · DirectML** for AI inference. Select **CPU** in the top-right if the graphics driver cannot run the model.

## Recolour a batch

1. Under **Colour reference**, choose a photo of the colour you want. Colour Shifter samples a representative colour from the foreground. Click the exact area in the reference if you want a different shade.
2. Add product photos or a folder. Click an item in the list to inspect it.
3. Optionally, click the original product where its current colour is visible. This limits changes to similar starting colours across the batch and is useful for products with contrasting trim. Use **Reset selection** to return to the whole foreground.
4. Select **Preview selected photo** and compare the result with the original. Choose an export folder and select **Recolour batch**.

Input formats are PNG, JPEG, WebP, BMP and single-frame TIFF. Folder selection adds supported files at the top level. Each batch creates a separate run folder with full-resolution PNGs, including any source transparency. If individual files fail, `errors.txt` lists them alongside the successful exports. **Stop batch** finishes the current photo and keeps the completed exports.

Colour Shifter works best when a clearly visible product fills a useful part of the frame. The model selects salient foreground objects, so a person, props or similar colours may also be selected. Preview representative photos, especially when the batch includes different scenes or products. The reference swatch anchors the material's midtone, and each photo's lighting influences the final shade.

## Model and runtime

Foreground segmentation uses [BiRefNet Lite](https://huggingface.co/ZhengPeng7/BiRefNet_lite) and the pinned [512-pixel ONNX export](https://huggingface.co/studioludens/birefnet-lite-512/tree/962e67969ec6acec9c19fab76acee4f5e22e990c). The model cards identify the weights and export as MIT-licensed. Setup fetches the model separately. [ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) runs the segmentation, and the app applies the colour transfer at the input resolution.
