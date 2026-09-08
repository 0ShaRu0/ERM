import os
import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk, messagebox

import database

try:
    from PIL import Image as pil_image
    from PIL import ImageTk as pil_image_tk
except ImportError:
    pil_image = None
    pil_image_tk = None


class RentDialog:
    def __init__(self, parent, equipment):
        self.result = None
        self.renters = {
            f"{r['user_id']} - {r['name']}": r["id"]
            for r in database.list_renters()
        }

        self.top = tk.Toplevel(parent)
        self.top.title("장비 대여")
        self.top.resizable(False, False)
        self.top.transient(parent.winfo_toplevel())
        self.top.grab_set()

        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text=(
                f"장비: {equipment['name']} "
                f"(대여 가능 {equipment['available_count']}/{equipment['quantity']}개)"
            ),
            font=("TkDefaultFont", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frm, text="대여자:").grid(row=1, column=0, sticky="w")
        self.renter_var = tk.StringVar()
        self.renter_combo = ttk.Combobox(
            frm, textvariable=self.renter_var, state="readonly", width=24
        )
        self.renter_combo.configure(values=list(self.renters))
        if self.renters:
            self.renter_combo.current(0)
        else:
            self.renter_var.set("등록된 대여자가 없음")
        self.renter_combo.grid(row=1, column=1, padx=(8, 0), pady=4)

        ttk.Label(frm, text="반납 예정일:").grid(row=2, column=0, sticky="w")
        self.due_var = tk.StringVar(
            value=(date.today() + timedelta(days=7)).isoformat()
        )
        due_entry = ttk.Entry(frm, textvariable=self.due_var, width=26)
        due_entry.grid(row=2, column=1, padx=(8, 0), pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="취소", command=self.top.destroy).pack(side="right")
        ttk.Button(btns, text="대여하기", command=self._confirm).pack(
            side="right", padx=(0, 6)
        )

        self.top.bind("<Return>", lambda e: self._confirm())
        self.top.bind("<Escape>", lambda e: self.top.destroy())

        self.top.update_idletasks()
        px, py = parent.winfo_toplevel().winfo_rootx(), parent.winfo_toplevel().winfo_rooty()
        pw, ph = parent.winfo_toplevel().winfo_width(), parent.winfo_toplevel().winfo_height()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        self.top.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 3}")

        due_entry.focus_set()

    def _confirm(self):
        label = self.renter_var.get()
        if label not in self.renters:
            messagebox.showwarning(
                "입력 확인", "대여자를 선택하세요.", parent=self.top
            )
            return
        due = self.due_var.get().strip()
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                messagebox.showwarning(
                    "입력 확인",
                    "반납 예정일은 YYYY-MM-DD 형식으로 입력하세요.",
                    parent=self.top,
                )
                return
        self.result = (self.renters[label], due)
        self.top.destroy()


class ReturnDialog:
    def __init__(self, parent, equipment, rentals):
        self.result = None
        self.rentals = {
            (
                f"대여 #{rental['id']} · {rental['user_id']} - {rental['renter_name']} "
                f"(대여일 {rental['rent_date']})"
            ): rental
            for rental in rentals
        }

        self.top = tk.Toplevel(parent)
        self.top.title("반납 대상 선택")
        self.top.resizable(False, False)
        self.top.transient(parent.winfo_toplevel())
        self.top.grab_set()

        frame = ttk.Frame(self.top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"장비: {equipment['name']}",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame, text="반납할 대여자를 선택하세요.", foreground="#555555"
        ).pack(anchor="w", pady=(4, 10))

        self.rental_var = tk.StringVar()
        rental_combo = ttk.Combobox(
            frame,
            textvariable=self.rental_var,
            values=list(self.rentals),
            state="readonly",
            width=48,
        )
        rental_combo.pack(fill="x")
        rental_combo.current(0)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="취소", command=self.top.destroy).pack(side="right")
        ttk.Button(buttons, text="선택 반납", command=self._confirm).pack(
            side="right", padx=(0, 6)
        )

        self.top.bind("<Return>", lambda e: self._confirm())
        self.top.bind("<Escape>", lambda e: self.top.destroy())
        self.top.update_idletasks()
        px = parent.winfo_toplevel().winfo_rootx()
        py = parent.winfo_toplevel().winfo_rooty()
        pw = parent.winfo_toplevel().winfo_width()
        ph = parent.winfo_toplevel().winfo_height()
        width = self.top.winfo_reqwidth()
        height = self.top.winfo_reqheight()
        self.top.geometry(f"+{px + (pw - width) // 2}+{py + (ph - height) // 3}")
        rental_combo.focus_set()

    def _confirm(self):
        self.result = self.rentals[self.rental_var.get()]
        self.top.destroy()


class HomeTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self._photo = None
        self._build()
        self.refresh()

    def _build(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(body, text="장비 목록", padding=6)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        toolbar = ttk.Frame(list_frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(
            toolbar,
            text="장비 추가/삭제 후에는 새로고침을 누르세요",
            foreground="#888888",
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="새로고침",
            command=lambda: self.app.user_refresh(self.refresh),
        ).pack(side="right")

        columns = ("id", "name", "category", "quantity", "available", "status")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        for col, text, width in [
            ("id", "번호", 50),
            ("name", "이름", 180),
            ("category", "분류", 120),
            ("quantity", "수량", 55),
            ("available", "대여 가능", 75),
            ("status", "상태", 90),
        ]:
            self.tree.heading(col, text=text)
            anchor = "center" if col in ("id", "status") else "w"
            self.tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.tag_configure("busy", foreground="#c0392b")
        self.tree.tag_configure("ok", foreground="#27ae60")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_preview())

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=0)

        preview_frame = ttk.LabelFrame(right, text="선택한 장비", padding=8)
        preview_frame.grid(row=0, column=0, sticky="nsew")
        self.preview_name = tk.StringVar(value="-")
        ttk.Label(
            preview_frame,
            textvariable=self.preview_name,
            font=("TkDefaultFont", 12, "bold"),
        ).pack(anchor="center", pady=(2, 0))
        self.status_label = ttk.Label(
            preview_frame, text="장비를 선택하세요", foreground="#555555"
        )
        self.status_label.pack(anchor="center")
        self.preview = ttk.Label(preview_frame, anchor="center")
        self.preview.pack(fill="both", expand=True, pady=(6, 0))

        btn_frame = ttk.LabelFrame(right, text="작업", padding=10)
        btn_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.rent_btn = ttk.Button(
            btn_frame, text="대 여 하 기", command=self._rent, padding=(10, 10)
        )
        self.rent_btn.pack(fill="x")
        self.return_btn = ttk.Button(
            btn_frame, text="반 납 하 기", command=self._give_back, padding=(10, 10)
        )
        self.return_btn.pack(fill="x", pady=(8, 0))

    def _selected_equipment(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("선택 확인", "장비를 선택하세요.", parent=self)
            return None
        return database.get_equipment(int(sel[0]))

    def _rent(self):
        eq = self._selected_equipment()
        if not eq:
            return
        if eq["available_count"] <= 0:
            messagebox.showwarning(
                "대여 불가",
                f"[{eq['name']}] 장비는 모두 대여 중입니다.",
                parent=self,
            )
            return
        dialog = RentDialog(self, eq)
        self.wait_window(dialog.top)
        if not dialog.result:
            return
        renter_id, due = dialog.result
        ok, msg = database.add_rental(eq["id"], renter_id, due)
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _give_back(self):
        eq = self._selected_equipment()
        if not eq:
            return
        rentals = database.list_active_rentals(eq["id"])
        if not rentals:
            messagebox.showwarning(
                "반납 불가",
                f"[{eq['name']}] 장비는 대여 중이 아닙니다.\n대여 중인 장비를 선택하세요.",
                parent=self,
            )
            return
        if len(rentals) == 1:
            rental = rentals[0]
        else:
            dialog = ReturnDialog(self, eq, rentals)
            self.wait_window(dialog.top)
            if not dialog.result:
                return
            rental = dialog.result
        if not messagebox.askyesno(
            "반납 확인",
            f"[{eq['name']}] 장비를 반납 처리하시겠습니까?\n"
            f"대여자: {rental['user_id']}\n"
            f"대여일: {rental['rent_date']}",
            parent=self,
        ):
            return
        ok, msg = database.return_rental(rental["id"])
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _show_preview(self):
        sel = self.tree.selection()
        if not sel:
            return
        eq = database.get_equipment(int(sel[0]))
        self.preview_name.set(eq["name"])
        available = eq["available_count"]
        status_color = "#27ae60" if available > 0 else "#c0392b"
        status_text = f"대여 가능 {available}/{eq['quantity']}개"
        self.status_label.configure(text=status_text, foreground=status_color)

        path = eq["image_path"]
        if not path or not os.path.exists(path):
            self.preview.configure(image="", text="이미지 없음")
            self._photo = None
            return
        if pil_image is None or pil_image_tk is None:
            self.preview.configure(image="", text="(Pillow 설치 시 미리보기)")
            return
        img = pil_image.open(path)
        img.thumbnail((260, 260))
        self._photo = pil_image_tk.PhotoImage(img)
        self.preview.configure(image=self._photo, text="")

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for row in database.list_equipment():
            tag = "ok" if row["available_count"] > 0 else "busy"
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["display_number"],
                    row["name"],
                    row["category"],
                    row["quantity"],
                    row["available_count"],
                    row["status"],
                ),
                tags=(tag,),
            )
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)
        else:
            self.preview_name.set("-")
            self.status_label.configure(
                text="장비를 선택하세요", foreground="#555555"
            )
            self.preview.configure(image="", text="")
            self._photo = None
