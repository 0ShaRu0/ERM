import tkinter as tk
from tkinter import ttk, messagebox

import database


class RenterTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self._build()
        self.refresh()

    def _build(self):
        top = ttk.LabelFrame(self, text="대여자 등록", padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="아이디:").grid(row=0, column=0, sticky="w")
        self.user_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.user_var, width=15).grid(
            row=0, column=1, padx=(4, 12)
        )

        ttk.Label(top, text="이름:").grid(row=0, column=2, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var, width=15).grid(
            row=0, column=3, padx=(4, 12)
        )

        ttk.Label(top, text="연락처:").grid(row=0, column=4, sticky="w")
        self.phone_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.phone_var, width=18).grid(
            row=0, column=5, padx=(4, 12)
        )
        ttk.Button(top, text="등록", command=self._add).grid(row=0, column=6)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(10, 0))

        search = ttk.Frame(body)
        search.pack(fill="x", pady=(0, 6))
        ttk.Label(search, text="검색:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var, width=25)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.refresh())
        ttk.Button(search, text="검색", command=self.refresh).pack(side="left")
        ttk.Button(search, text="삭제", command=self._delete).pack(side="right")

        columns = ("user_id", "name", "phone", "created_at")
        self.tree = ttk.Treeview(
            self, columns=columns, show="headings", selectmode="browse"
        )
        for col, text, width in [
            ("user_id", "아이디", 130),
            ("name", "이름", 130),
            ("phone", "연락처", 160),
            ("created_at", "등록일", 160),
        ]:
            self.tree.heading(col, text=text)
            anchor = "center" if col == "phone" else "w"
            self.tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(fill="both", expand=True)

    def _add(self):
        user_id = self.user_var.get().strip()
        name = self.name_var.get().strip()
        if not user_id or not name:
            messagebox.showwarning("입력 확인", "아이디와 이름을 입력하세요.", parent=self)
            return
        ok, msg = database.add_renter(user_id, name, self.phone_var.get().strip())
        if ok:
            self.user_var.set("")
            self.name_var.set("")
            self.phone_var.set("")
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("선택 확인", "삭제할 대여자를 선택하세요.", parent=self)
            return
        values = self.tree.item(sel[0], "values")
        if not messagebox.askyesno(
            "삭제 확인", f"[{values[0]}] 대여자를 삭제하시겠습니까?", parent=self
        ):
            return
        ok, msg = database.delete_renter(int(sel[0]))
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def refresh(self):
        keyword = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        self.tree.delete(*self.tree.get_children())
        for row in database.list_renters(keyword):
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["user_id"],
                    row["name"],
                    row["phone"],
                    row["created_at"],
                ),
            )
