"""Colour Shifter desktop application."""

import os
import sqlite3
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .catalogue import export_catalogue
from .images import IMAGE_EXTENSIONS, folder_images, open_image
from .recolour import Segmenter, apply_colour, export_batch, prepare_recolour, reference_colour, sample_colour
from .swatch_dialog import SwatchDialog
from .swatches import hex_colour, remember_batch_colour


BACKGROUND = "#141822"
PANEL = "#202635"
WELL = "#191e2a"
TEXT = "#f3f5fa"
MUTED = "#b1bdd2"
ACCENT = "#b8f4d5"
FONT = "Segoe UI"
GPU = "GPU · DirectML"
FILE_TYPES = [("Images", " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS))), ("All files", "*.*")]


class Photo(ttk.Frame):
    def __init__(self, parent, title, placeholder, clicked=None, height=140):
        super().__init__(parent, padding=8, style="Panel.TFrame")
        self.caption = ttk.Label(self, text=title, style="Panel.TLabel", font=(FONT, 10, "bold"))
        self.caption.pack(anchor="w", pady=(0, 6))
        self.canvas = tk.Canvas(self, background=WELL, borderwidth=0, highlightthickness=0, width=1, height=height)
        self.canvas.pack(fill="both", expand=True)
        self.placeholder = placeholder
        self.image = None
        self.marker = None
        self.thumbnail = None
        self.bounds = None
        self.clicked = clicked
        self.canvas.bind("<Configure>", self.draw)
        if clicked is not None:
            self.canvas.bind("<Button-1>", self.pick)
            self.canvas.configure(cursor="crosshair")

    def show(self, image, marker=None):
        self.image = image
        self.marker = marker
        self.draw()

    def draw(self, event=None):
        self.canvas.delete("all")
        self.bounds = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.image is None:
            self.thumbnail = None
            self.canvas.create_text(
                width // 2, height // 2, text=self.placeholder, fill=MUTED, font=(FONT, 10),
                width=max(1, width - 24), justify="center",
            )
            return

        scale = min(max(1, width - 8) / self.image.width, max(1, height - 8) / self.image.height)
        size = tuple(max(1, round(dimension * scale)) for dimension in self.image.size)
        preview = self.image.resize(size, Image.Resampling.LANCZOS)
        self.thumbnail = ImageTk.PhotoImage(preview)
        x = (width - preview.width) // 2
        y = (height - preview.height) // 2
        self.bounds = (x, y, preview.width, preview.height)
        self.canvas.create_image(x, y, anchor="nw", image=self.thumbnail)
        if self.marker:
            px = x + (self.marker[0] + 0.5) * preview.width / self.image.width
            py = y + (self.marker[1] + 0.5) * preview.height / self.image.height
            self.canvas.create_oval(px - 7, py - 7, px + 7, py + 7, outline="#141822", width=4)
            self.canvas.create_oval(px - 7, py - 7, px + 7, py + 7, outline="white", width=2)

    def pick(self, event):
        if self.bounds is None:
            return
        x, y, width, height = self.bounds
        dx, dy = event.x - x, event.y - y
        if 0 <= dx < width and 0 <= dy < height:
            self.clicked(dx * self.image.width // width, dy * self.image.height // height)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Colour Shifter")
        self.geometry("1180x760")
        self.minsize(900, 710)
        self.configure(background=BACKGROUND)
        self.paths = []
        self.target = None
        self.source_overrides = {}
        self.preview_result = None
        self.preview_mask = None
        self.output = None
        self.last_export = None
        self.busy = False
        self.cancel_batch = Event()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.events = SimpleQueue()
        self.engine = None
        self.engine_cpu = None
        self.backend = tk.StringVar(value=GPU)
        self.status = tk.StringVar(value="Choose a reference or swatch and add product photos to begin.")
        self.target_text = tk.StringVar(value="No colour selected")
        self.source_text = tk.StringVar(value="Starting colour · automatic")
        self.show_mask = tk.BooleanVar(value=False)
        self.catalogue_suffix = tk.BooleanVar(value=True)
        self.output_text = tk.StringVar(value="Choose where to save the batch.")
        self.batch_text = tk.StringVar(value="2   Product photos")
        self.configure_styles()
        self.build()
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=BACKGROUND, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Panel.TLabel", background=PANEL)
        style.configure("TButton", background=PANEL, foreground=TEXT, bordercolor="#414d63", padding=(8, 5))
        style.map("TButton", background=[("active", "#323c51")], foreground=[("disabled", "#8390a6")])
        style.configure("Accent.TButton", background=ACCENT, foreground=BACKGROUND, bordercolor=ACCENT, padding=(12, 8))
        style.map(
            "Accent.TButton", background=[("disabled", PANEL), ("active", "#d3f9e6")],
            foreground=[("disabled", "#8390a6")],
        )
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, bordercolor=PANEL)
        style.configure("Treeview", background=WELL, fieldbackground=WELL, foreground=TEXT)
        style.configure("Treeview.Heading", background=PANEL, foreground=TEXT)
        style.map("Treeview", background=[("selected", "#3c516b")], foreground=[("selected", TEXT)])

    def build(self):
        outer = ttk.Frame(self, padding=(20, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="Colour Shifter", font=(FONT, 22, "bold")).pack(anchor="w")
        ttk.Label(header, text="Offline product recolouring from a reference photo", style="Muted.TLabel").pack(anchor="w")
        self.backend_picker = ttk.Combobox(header, textvariable=self.backend, values=(GPU, "CPU"), state="readonly", width=16)
        self.backend_picker.place(relx=1, rely=0.5, anchor="e")

        controls = ttk.Frame(outer, width=295)
        controls.grid(row=1, column=0, sticky="ns", padx=(0, 18))
        controls.pack_propagate(False)

        reference_bar = ttk.Frame(controls)
        reference_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(reference_bar, text="1   Colour reference", font=(FONT, 11, "bold")).pack(side="left")
        self.reference_button = ttk.Button(reference_bar, text="Choose image…", command=self.choose_reference)
        self.reference_button.pack(side="right")
        self.reference = Photo(controls, "Reference · click to sample", "Add a reference photo", self.pick_reference, height=112)
        self.reference.pack(fill="x")
        target_bar = ttk.Frame(controls)
        target_bar.pack(fill="x", pady=(7, 12))
        self.target_swatch = tk.Label(target_bar, width=3, background=WELL)
        self.target_swatch.pack(side="left", padx=(0, 8))
        ttk.Label(target_bar, textvariable=self.target_text).pack(side="left")
        self.swatches_button = ttk.Button(target_bar, text="Swatch library…", command=self.open_swatches)
        self.swatches_button.pack(side="right")

        ttk.Label(controls, textvariable=self.batch_text, font=(FONT, 11, "bold")).pack(anchor="w")
        file_bar = ttk.Frame(controls)
        file_bar.pack(fill="x", pady=(5, 5))
        self.files_button = ttk.Button(file_bar, text="Add photos…", command=self.choose_files)
        self.files_button.pack(side="left", padx=(0, 4))
        self.folder_button = ttk.Button(file_bar, text="Add folder…", command=self.choose_folder)
        self.folder_button.pack(side="left", padx=(0, 4))
        self.clear_button = ttk.Button(file_bar, text="Clear", command=self.clear_files)
        self.clear_button.pack(side="left")
        list_frame = ttk.Frame(controls)
        list_frame.pack(fill="both", expand=True)
        self.file_list = tk.Listbox(
            list_frame, height=4, borderwidth=0, highlightthickness=1, highlightbackground="#414d63",
            background=WELL, foreground=TEXT, selectbackground="#3c516b", selectforeground=TEXT,
            font=(FONT, 10), exportselection=False, activestyle="none",
        )
        self.file_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.file_list.configure(yscrollcommand=scrollbar.set)
        self.file_list.bind("<<ListboxSelect>>", self.select_input)

        output_bar = ttk.Frame(controls)
        output_bar.pack(fill="x", pady=(11, 4))
        ttk.Label(output_bar, text="3   Export folder", font=(FONT, 11, "bold")).pack(side="left")
        self.output_button = ttk.Button(output_bar, text="Choose folder…", command=self.choose_output)
        self.output_button.pack(side="right")
        ttk.Label(controls, textvariable=self.output_text, style="Muted.TLabel", wraplength=290).pack(anchor="w")
        self.catalogue_button = ttk.Button(controls, text="Export 5-colour catalogue…", command=self.choose_catalogue)
        self.catalogue_button.pack(fill="x", pady=(12, 4))
        self.suffix_check = ttk.Checkbutton(controls, text="Append colour name to filenames", variable=self.catalogue_suffix)
        self.suffix_check.pack(anchor="w")

        preview = ttk.Frame(outer)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure((0, 1), weight=1, uniform="photo")
        preview.rowconfigure(1, weight=1)
        preview_bar = ttk.Frame(preview)
        preview_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(preview_bar, text="Batch preview", font=(FONT, 13, "bold")).pack(side="left")
        self.preview_button = ttk.Button(preview_bar, text="Preview selected photo", command=self.make_preview)
        self.preview_button.pack(side="right")
        self.original = Photo(preview, "Original", "Select a photo from the batch", self.pick_source)
        self.original.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.result = Photo(preview, "Recoloured", "Preview the selected photo to see the result")
        self.result.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        source_bar = ttk.Frame(preview)
        source_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(source_bar, textvariable=self.source_text, style="Muted.TLabel").pack(side="left")
        self.clear_source_button = ttk.Button(source_bar, text="Use automatic colour", command=self.clear_source)
        self.clear_source_button.pack(side="right")
        self.mask_check = ttk.Checkbutton(source_bar, text="Show mask", variable=self.show_mask, command=self.display_preview)
        self.mask_check.pack(side="right", padx=(0, 10))
        ttk.Label(
            preview, text="Click the original to override the starting colour for this photo.",
            style="Muted.TLabel", wraplength=500,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(1, 0))

        footer = ttk.Frame(outer)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=540).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.open_button = ttk.Button(footer, text="Open exports", command=self.open_exports)
        self.open_button.grid(row=0, column=1, padx=(0, 6))
        self.cancel_button = ttk.Button(footer, text="Stop batch", command=self.stop)
        self.cancel_button.grid(row=0, column=2, padx=(0, 6))
        self.run_button = ttk.Button(footer, text="Recolour batch", style="Accent.TButton", command=self.run_batch)
        self.run_button.grid(row=0, column=3)
        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))

    def refresh(self):
        enabled = not self.busy
        has_input = self.original.image is not None and self.target is not None
        for button, condition in (
            (self.reference_button, True), (self.swatches_button, True), (self.files_button, True), (self.folder_button, True),
            (self.clear_button, bool(self.paths)), (self.output_button, True), (self.preview_button, has_input),
            (self.clear_source_button, self.selected_path() in self.source_overrides),
            (self.mask_check, self.preview_mask is not None), (self.catalogue_button, True), (self.suffix_check, True),
            (self.open_button, self.last_export is not None),
            (self.run_button, bool(self.paths) and self.target is not None and self.output is not None),
        ):
            button.configure(state="normal" if enabled and condition else "disabled")
        self.backend_picker.configure(state="readonly" if enabled else "disabled")
        self.file_list.configure(state="normal" if enabled else "disabled")
        self.cancel_button.configure(state="disabled")

    def get_engine(self, cpu):
        if self.engine is None or cpu != self.engine_cpu:
            self.engine = None
            self.engine = Segmenter(cpu=cpu)
            self.engine_cpu = cpu
        return self.engine

    def launch(self, job, finished, status, *, batch=False, failed=None):
        self.busy = True
        self.status.set(status)
        self.refresh()
        if batch:
            self.cancel_button.configure(state="normal")
        cpu = self.backend.get() == "CPU"
        future = self.executor.submit(lambda: job(self.get_engine(cpu)))

        def poll():
            while True:
                try:
                    count, total, status_text = self.events.get_nowait()
                except Empty:
                    break
                self.progress.configure(value=count, maximum=total)
                self.status.set(status_text)
            if not future.done():
                self.after(80, poll)
                return

            self.busy = False
            try:
                result = future.result()
            except Exception as error:
                self.status.set(failed or str(error))
                if failed is None:
                    messagebox.showerror("Colour Shifter", str(error), parent=self)
            else:
                finished(result)
            self.refresh()

        self.after(80, poll)

    def choose_reference(self):
        path = filedialog.askopenfilename(parent=self, title="Choose a colour reference", filetypes=FILE_TYPES)
        if not path:
            return
        try:
            image = open_image(Path(path))
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showerror("Could not open reference", str(error), parent=self)
            return
        self.reference.show(image)
        self.set_target(None)
        self.launch(
            lambda engine: reference_colour(image, engine.foreground(image)),
            self.set_target,
            "Finding a representative colour in the reference…",
            failed="Click the reference image to sample a colour manually.",
        )

    def set_target(self, colour):
        self.target = colour
        self.target_text.set(hex_colour(colour) if colour is not None else "Click to sample")
        self.target_swatch.configure(background=hex_colour(colour) if colour is not None else WELL)
        self.invalidate_preview()
        if colour is not None:
            self.status.set("Reference colour ready. Select a photo to preview or recolour the batch.")
        self.refresh()

    def pick_reference(self, x, y):
        if self.busy:
            return
        try:
            self.set_target(sample_colour(self.reference.image, x, y))
            self.reference.show(self.reference.image, (x, y))
        except ValueError as error:
            self.status.set(str(error))

    def open_swatches(self):
        try:
            SwatchDialog(self, self.target, self.use_swatch)
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror("Could not open swatch library", str(error), parent=self)

    def use_swatch(self, colour, name):
        self.reference.show(self.reference.image)
        self.set_target(colour)
        self.status.set(f"Using swatch {name}. Preview a photo or recolour the batch.")

    def choose_files(self):
        files = filedialog.askopenfilenames(parent=self, title="Add product photos", filetypes=FILE_TYPES)
        self.add_files([Path(file) for file in files])

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Add photos from a folder", mustexist=True)
        if folder:
            try:
                paths = folder_images(Path(folder))
            except OSError as error:
                messagebox.showerror("Could not open folder", str(error), parent=self)
                return
            if paths:
                self.add_files(paths)
            else:
                self.status.set("The folder contains no supported images.")

    def add_files(self, paths):
        known = set(self.paths)
        for path in paths:
            if path not in known:
                self.paths.append(path)
                known.add(path)
                self.file_list.insert("end", path.name)
        if self.paths and not self.file_list.curselection():
            self.file_list.selection_set(0)
            self.select_input()
        self.batch_text.set(f"2   Product photos ({len(self.paths)})")
        self.refresh()

    def clear_files(self):
        self.paths.clear()
        self.source_overrides.clear()
        self.file_list.delete(0, "end")
        self.batch_text.set("2   Product photos")
        self.original.show(None)
        self.source_text.set("Starting colour · automatic")
        self.invalidate_preview()
        self.refresh()

    def selected_path(self):
        indices = self.file_list.curselection()
        return self.paths[indices[0]] if indices else None

    def select_input(self, event=None):
        path = self.selected_path()
        if self.busy or path is None:
            return
        self.invalidate_preview()
        sample = self.source_overrides.get(path)
        self.source_text.set(f"Starting colour · override {hex_colour(sample[0])}" if sample else "Starting colour · automatic")
        try:
            self.original.show(open_image(path), sample[1] if sample else None)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.original.show(None)
            self.status.set(f"{path.name} · {error}")
        self.refresh()

    def pick_source(self, x, y):
        if self.busy:
            return
        try:
            source = sample_colour(self.original.image, x, y)
        except ValueError as error:
            self.status.set(str(error))
            return
        self.source_overrides[self.selected_path()] = (source, (x, y))
        self.source_text.set(f"Starting colour · override {hex_colour(source)}")
        self.original.show(self.original.image, (x, y))
        self.invalidate_preview()
        self.refresh()

    def clear_source(self):
        self.source_overrides.pop(self.selected_path(), None)
        self.source_text.set("Starting colour · automatic")
        self.original.show(self.original.image)
        self.invalidate_preview()
        self.refresh()

    def choose_output(self):
        folder = filedialog.askdirectory(parent=self, title="Choose export folder", mustexist=True)
        if folder:
            self.output = Path(folder)
            self.output_text.set(str(self.output))
            self.refresh()

    def make_preview(self):
        image, target = self.original.image, self.target
        sample = self.source_overrides.get(self.selected_path())

        def job(engine):
            source, mask, reflections = prepare_recolour(engine, image, sample[0] if sample else None)
            return source, mask, apply_colour(image, mask, target, reflections=reflections)

        def finished(result):
            source, self.preview_mask, self.preview_result = result
            mode = "override" if sample else "detected"
            self.source_text.set(f"Starting colour · {mode} {hex_colour(source)}")
            self.display_preview()
            self.status.set("Preview ready. Use Show mask to inspect the selected material.")

        self.launch(job, finished, "Detecting starting colour and masking the product…")

    def invalidate_preview(self):
        self.preview_result = self.preview_mask = None
        self.display_preview()

    def display_preview(self):
        show_mask = self.show_mask.get() and self.preview_mask is not None
        self.result.caption.configure(text="Mask · selected product surface" if show_mask else "Recoloured")
        self.result.show(self.preview_mask if show_mask else self.preview_result)

    def run_batch(self):
        paths, output, target = tuple(self.paths), self.output, self.target
        overrides = {path: sample[0] for path, sample in self.source_overrides.items()}
        self.cancel_batch.clear()
        self.progress.configure(maximum=len(paths), value=0)

        def finished(result):
            (self.last_export, count, failures), warning = result
            summary = f"Exported {count} of {len(paths)} photos."
            if self.cancel_batch.is_set() and count + len(failures) < len(paths):
                summary += " Batch stopped."
            if failures:
                summary += f" {len(failures)} failed. See errors.txt in the export folder."
            if warning:
                summary += f" {warning}"
            self.status.set(summary)

        def job(engine):
            result = export_batch(
                engine, paths, output, target, None,
                lambda count, status: self.events.put((count, len(paths), status)), self.cancel_batch.is_set,
                overrides=overrides,
            )
            return result, remember_batch_colour(target) if result[1] else None

        self.launch(
            job, finished, "Preparing the batch…", batch=True,
        )

    def choose_catalogue(self):
        source = filedialog.askdirectory(parent=self, title="Choose the catalogue folder and its subfolders", mustexist=True)
        if not source:
            return
        references = filedialog.askdirectory(parent=self, title="Choose the folder containing the five colour reference shots",
                                              initialdir=source, mustexist=True)
        if not references:
            return
        destination = filedialog.askdirectory(parent=self, title="Choose a separate destination for the colourways",
                                              initialdir=self.output, mustexist=True)
        if destination:
            self.run_catalogue(Path(source), Path(references), Path(destination))

    def run_catalogue(self, root, references, destination):
        suffix = self.catalogue_suffix.get()
        overrides = {path.resolve(): sample[0] for path, sample in self.source_overrides.items()}
        self.cancel_batch.clear()
        self.progress.configure(maximum=1, value=0)

        def job(engine):
            return export_catalogue(
                engine, root, references, destination, suffix=suffix, overrides=overrides,
                progress=lambda count, total, status: self.events.put((count, total, status)),
                cancelled=self.cancel_batch.is_set,
            )

        def finished(result):
            self.last_export = result.folder
            summary = f"Exported {result.variants} versions. {result.completed} of {result.photos} photos have all five."
            if result.failed or self.cancel_batch.is_set():
                summary += " See manifest.json for incomplete photos."
            self.status.set(summary)

        self.launch(job, finished, "Discovering the five reference colours…", batch=True)

    def stop(self):
        self.cancel_batch.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Stopping after the current photo…")

    def open_exports(self):
        try:
            os.startfile(self.last_export)
        except OSError as error:
            messagebox.showerror("Could not open exports", str(error), parent=self)

    def close(self):
        if self.busy:
            self.status.set("Wait for the current operation or stop the batch before closing.")
            return
        self.executor.shutdown(wait=False)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
