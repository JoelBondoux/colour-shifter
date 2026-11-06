"""Desktop controls for the shared swatch library."""

import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .swatches import SwatchStore, hex_colour, parse_hex


class SwatchDialog(tk.Toplevel):
    def __init__(self, parent, current, apply):
        self.store = SwatchStore()
        saved, recent = self.store.snapshot()
        super().__init__(parent)
        self.title("Colour Shifter · Swatch library")
        self.transient(parent)
        self.minsize(630, 360)
        self.current = hex_colour(current) if current is not None else None
        self.apply = apply
        self.icons = []

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure((0, 1), weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="Saved swatches", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Recent batch colours", font=("Segoe UI", 11, "bold")).grid(row=0, column=1, sticky="w")
        self.saved = self.table(frame, 0, "Name")
        self.recent = self.table(frame, 1, "Last 20 colours used")
        self.saved.bind("<Double-1>", lambda event: self.use(self.saved) if self.saved.identify_row(event.y) else None)
        self.recent.bind("<Double-1>", lambda event: self.use(self.recent) if self.recent.identify_row(event.y) else None)

        saved_actions = ttk.Frame(frame)
        saved_actions.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.button(saved_actions, "Add…", self.add)
        self.button(saved_actions, "Rename…", self.rename)
        self.button(saved_actions, "Delete…", self.delete)
        self.button(saved_actions, "Use", lambda: self.use(self.saved))
        recent_actions = ttk.Frame(frame)
        recent_actions.grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.button(recent_actions, "Save as swatch…", self.save_recent)
        self.button(recent_actions, "Use", lambda: self.use(self.recent))

        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        save_current = ttk.Button(footer, text="Save current colour…", command=lambda: self.save(self.current))
        save_current.pack(side="left")
        if self.current is None:
            save_current.state(["disabled"])
        else:
            ttk.Label(footer, text=self.current).pack(side="left", padx=6)
        ttk.Button(footer, text="Close", command=self.destroy).pack(side="right")

        self.populate(saved, recent)
        self.bind("<Escape>", lambda event: self.destroy())
        self.wait_visibility()
        self.grab_set()
        self.saved.focus_set()

    def table(self, parent, column, heading):
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=column, sticky="nsew", padx=(0, 8) if column == 0 else 0, pady=(6, 0))
        tree = ttk.Treeview(frame, columns=("hex",) if column == 0 else (), selectmode="browse", height=9)
        tree.heading("#0", text=heading, anchor="w")
        tree.column("#0", width=185, minwidth=120)
        if column == 0:
            tree.heading("hex", text="Hex", anchor="w")
            tree.column("hex", width=76, minwidth=76, stretch=False)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)
        return tree

    def button(self, parent, text, command):
        ttk.Button(parent, text=text, width=0, command=command).pack(side="left", padx=(0, 5))

    def populate(self, saved, recent, select=None):
        for tree in (self.saved, self.recent):
            tree.delete(*tree.get_children())
        self.icons.clear()
        for swatch in saved:
            item = self.saved.insert("", "end", text=swatch.name, image=self.chip(swatch.hex), values=(swatch.hex,))
            if swatch.name == select:
                self.saved.selection_set(item)
                self.saved.see(item)
        for colour in recent:
            self.recent.insert("", "end", text=colour, image=self.chip(colour))

    def chip(self, colour):
        icon = tk.PhotoImage(master=self, width=14, height=14)
        icon.put("#aeb6c6", to=(0, 0, 14, 14))
        icon.put(colour, to=(1, 1, 13, 13))
        self.icons.append(icon)
        return icon

    def selected(self, tree):
        items = tree.selection()
        if not items:
            messagebox.showinfo("Swatch library", "Select a colour first.", parent=self)
            return None
        return tree.item(items[0])

    def use(self, tree):
        item = self.selected(tree)
        if item:
            name = item["text"]
            colour = str(item["values"][0]) if tree is self.saved else name
            self.destroy()
            self.apply(parse_hex(colour), name)

    def add(self):
        colour = simpledialog.askstring(
            "Add swatch", "Enter a six-digit hex colour.", initialvalue=self.current or "#", parent=self
        )
        if colour is not None:
            try:
                parse_hex(colour)
            except ValueError as error:
                messagebox.showerror("Swatch library", str(error), parent=self)
                return
            self.save(colour)

    def save_recent(self):
        item = self.selected(self.recent)
        if item:
            self.save(item["text"])

    def save(self, colour):
        name = simpledialog.askstring("Save swatch", f"Name for {colour}", parent=self)
        if name is not None:
            self.change(lambda: self.store.add(name, colour))

    def rename(self):
        item = self.selected(self.saved)
        if item:
            name = item["text"]
            replacement = simpledialog.askstring("Rename swatch", "New name", initialvalue=name, parent=self)
            if replacement is not None:
                self.change(lambda: self.store.rename(name, replacement))

    def delete(self):
        item = self.selected(self.saved)
        if item and messagebox.askyesno("Delete swatch", f"Delete {item['text']} from the library?", parent=self):
            self.change(lambda: self.store.delete(item["text"]))

    def change(self, operation):
        try:
            swatch = operation()
            self.populate(*self.store.snapshot(), select=swatch.name if swatch else None)
        except (OSError, sqlite3.Error, ValueError) as error:
            messagebox.showerror("Swatch library", str(error), parent=self)
