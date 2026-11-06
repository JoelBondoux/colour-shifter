# Colour Shifter

Recolour a batch of product photos using a saved swatch or a colour sampled from a reference image. Colour Shifter is a Windows desktop app that uses local AI to find the foreground product. It transfers the colour while retaining the shading, highlights and texture visible in the original photos.

## Start on Windows

1. Download **Colour-Shifter-Windows-x64.zip** from the [latest GitHub release](https://github.com/JoelBondoux/colour-shifter/releases/latest).
2. Extract the complete archive to a folder. Open **Colour Shifter.exe** inside the extracted **Colour Shifter** folder.
3. Keep the executables and the `_internal` folder together. The package includes Python, the GPU runtime and the AI model for offline use from the first launch.

Use x64 Windows 10 or 11. The app defaults to **GPU · DirectML** for AI inference on a DirectX 12-capable graphics driver. Select **CPU** in the top-right if the graphics driver cannot run the model.

## Recolour a batch

1. Use **Swatch library** to select a saved colour, or choose a reference photo. Colour Shifter samples a representative colour from the reference foreground. Click the exact area in the photo if you want a different shade.
2. Add product photos or a folder. Click an item in the list to inspect it.
3. Optionally, click the original product where its current colour is visible. This limits changes to similar starting colours across the batch and is useful for products with contrasting trim. Use **Reset selection** to return to the whole foreground.
4. Select **Preview selected photo** and compare the result with the original. Choose an export folder and select **Recolour batch**.

Input formats are PNG, JPEG, WebP, BMP and single-frame TIFF. Folder selection adds supported files at the top level. Each batch creates a separate run folder with full-resolution PNGs, including any source transparency. If individual files fail, `errors.txt` lists them alongside the successful exports. **Stop batch** finishes the current photo and keeps the completed exports.

Colour Shifter works best when a clearly visible product fills a useful part of the frame. The model selects salient foreground objects, so a person, props or similar colours may also be selected. Preview representative photos, especially when the batch includes different scenes or products. The reference swatch anchors the material's midtone, and each photo's lighting influences the final shade.

## Reuse swatches

Open **Swatch library** to add a colour by its hex code, rename or delete a swatch, or save the current reference colour. Each batch that exports at least one image adds its target to **Recent batch colours**. Select any of the last 20 distinct colours and choose **Save as swatch** to give it a name. Use a saved or recent colour to set the next batch's target.

The desktop app and MCP share the library in `%LOCALAPPDATA%\Colour Shifter\swatches.sqlite3`. The library is separate from the extracted program folder, so it stays available when the app is updated or moved.

## Local MCP API

Configure a local MCP client to launch **Colour Shifter MCP.exe** from the same extracted folder using the stdio transport. The client starts the server when needed. For a client that accepts an `mcpServers` configuration, use the full path to the extracted executable.

```json
{
  "mcpServers": {
    "colour-shifter": {
      "command": "C:\\Apps\\Colour Shifter\\Colour Shifter MCP.exe"
    }
  }
}
```

| Tool | What it does |
| --- | --- |
| `list_swatches` | Lists named swatches and recent batch colours. |
| `add_swatch`, `rename_swatch`, `delete_swatch` | Manage named swatches in the shared library. |
| `sample_reference` | Reads a colour from a reference photo using the AI-selected foreground or supplied pixel coordinates. |
| `recolour_batch` | Exports a batch using a swatch name, hex colour or reference photo. Returns the output folder and counts. |

Use absolute paths to images and folders on the machine running the server. For example, ask the client to recolour the product photos in a specified folder using the saved **Dusty Rose** swatch and export to an existing destination folder. The batch tool creates a new subfolder and leaves the originals in place. GPU is the default. Pass `backend: "cpu"` to use CPU inference.

## Run or build from source

Install x64 [Python 3.11](https://www.python.org/downloads/windows/) with the Python launcher and Tkinter. Run **Setup.cmd** to install dependencies and download the verified AI model. Open **Start.cmd** for the desktop app, or launch the local MCP server with `\.venv\Scripts\python.exe -m colour_shifter.mcp_server`.

To build the portable Windows package from that environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\Build.ps1
```

The build puts the release ZIP and its SHA-256 checksum in `dist`.

## Model and runtime

Foreground segmentation uses [BiRefNet Lite](https://huggingface.co/ZhengPeng7/BiRefNet_lite) and the pinned [512-pixel ONNX export](https://huggingface.co/studioludens/birefnet-lite-512/tree/962e67969ec6acec9c19fab76acee4f5e22e990c). The Windows bundle includes the model's MIT license and the runtime's third-party notices in `_internal\licenses`. [ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) runs the segmentation, and the app applies the colour transfer at the input resolution.
