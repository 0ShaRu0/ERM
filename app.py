import tkinter as tk
import time
from tkinter import ttk

import database
from tabs.home_tab import HomeTab
from tabs.equipment_tab import EquipmentTab
from tabs.rental_tab import RentalTab
from tabs.renter_tab import RenterTab
from tabs.stats_tab import StatsTab

REFRESH_CLICK_COUNT = 10
REFRESH_WINDOW_SECONDS = 10
RAINBOW_DURATION_SECONDS = 10
RAINBOW_INTERVAL_MS = 120
RAINBOW_COLORS = (
    "#FFB3BA",
    "#FFD7A8",
    "#FFF3A6",
    "#B9F6CA",
    "#A7E8FF",
    "#B5C7FF",
    "#D7B5FF",
    "#FFB8E0",
)


class RentalApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("장비 대여 관리")
        self.geometry("1080x700")
        self.minsize(940, 620)
        self._refresh_clicks = []
        self._rainbow_job = None
        self._rainbow_end = 0
        self._rainbow_index = 0
        self._style = ttk.Style(self)
        self._theme_before_rainbow = self._style.theme_use()
        self._background_before_rainbow = self.cget("background")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.home_tab = HomeTab(notebook, self)
        self.equipment_tab = EquipmentTab(notebook, self)
        self.rental_tab = RentalTab(notebook, self)
        self.renter_tab = RenterTab(notebook, self)
        self.stats_tab = StatsTab(notebook, self)

        notebook.add(self.home_tab, text=" 홈 ")
        notebook.add(self.equipment_tab, text=" 장비 관리 ")
        notebook.add(self.rental_tab, text=" 대여 관리 ")
        notebook.add(self.renter_tab, text=" 대여자 관리 ")
        notebook.add(self.stats_tab, text=" 대여 통계 ")

    def refresh_all(self):
        self.home_tab.refresh()
        self.equipment_tab.refresh()
        self.rental_tab.refresh()
        self.renter_tab.refresh()
        self.stats_tab.refresh()

    def user_refresh(self, callback):
        callback()
        now = time.monotonic()
        self._refresh_clicks = [
            clicked_at
            for clicked_at in self._refresh_clicks
            if now - clicked_at <= REFRESH_WINDOW_SECONDS
        ]
        self._refresh_clicks.append(now)
        if len(self._refresh_clicks) >= REFRESH_CLICK_COUNT:
            self._refresh_clicks.clear()
            self._start_rainbow()

    def _start_rainbow(self):
        if self._rainbow_job is None:
            self._theme_before_rainbow = self._style.theme_use()
            self._background_before_rainbow = self.cget("background")
            if "rainbow" not in self._style.theme_names():
                self._style.theme_create("rainbow", parent="clam")
            self._style.theme_use("rainbow")
        else:
            self.after_cancel(self._rainbow_job)
        self._rainbow_end = time.monotonic() + RAINBOW_DURATION_SECONDS
        self._rainbow_step()

    def _rainbow_step(self):
        if time.monotonic() >= self._rainbow_end:
            self._style.theme_use(self._theme_before_rainbow)
            self.configure(background=self._background_before_rainbow)
            self._rainbow_job = None
            return

        color = RAINBOW_COLORS[self._rainbow_index % len(RAINBOW_COLORS)]
        self._rainbow_index += 1
        self.configure(background=color)
        for style_name in (
            ".",
            "TFrame",
            "TLabel",
            "TLabelframe",
            "TLabelframe.Label",
            "TNotebook",
            "TNotebook.Tab",
            "TButton",
            "TCheckbutton",
            "TRadiobutton",
            "Treeview",
            "Treeview.Heading",
            "Vertical.TScrollbar",
            "Horizontal.TScrollbar",
        ):
            self._style.configure(style_name, background=color)
        self._style.configure("TEntry", fieldbackground=color)
        self._style.configure("TCombobox", fieldbackground=color)
        self._style.configure("Treeview", fieldbackground=color)
        self._rainbow_job = self.after(RAINBOW_INTERVAL_MS, self._rainbow_step)


def main():
    database.init_db()
    app = RentalApp()
    app.mainloop()


if __name__ == "__main__":
    main()
