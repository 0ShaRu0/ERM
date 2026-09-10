from tkinter import ttk

import database


class RentalTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self._build()
        self.refresh()

    def _build(self):
        inner = ttk.Notebook(self)
        inner.pack(fill="both", expand=True)

        active_frame = ttk.Frame(inner, padding=6)
        history_frame = ttk.Frame(inner, padding=6)
        inner.add(active_frame, text=" 대여 현황 ")
        inner.add(history_frame, text=" 대여 이력 ")

        columns = ("equip", "user", "name", "rent_date", "due")
        self.active_tree = self._make_tree(
            active_frame,
            columns,
            [
                ("equip", "장비명", 160),
                ("user", "대여자 아이디", 110),
                ("name", "이름", 100),
                ("rent_date", "대여일시", 150),
                ("due", "반납 예정일", 100),
            ],
        )
        self._add_refresh_toolbar(active_frame)

        h_columns = ("id", "equip", "user", "rent_date", "due", "return_date")
        self.history_tree = self._make_tree(
            history_frame,
            h_columns,
            [
                ("id", "번호", 50),
                ("equip", "장비명", 160),
                ("user", "대여자 아이디", 110),
                ("rent_date", "대여일시", 150),
                ("due", "반납 예정일", 100),
                ("return_date", "반납일시", 150),
            ],
        )
        self._add_refresh_toolbar(history_frame)

    def _add_refresh_toolbar(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(6, 0))
        ttk.Button(
            toolbar,
            text="새로고침",
            command=lambda: self.app.user_refresh(self.refresh),
        ).pack(side="right")

    def _make_tree(self, parent, columns, specs):
        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=columns, show="headings")
        for col, text, width in specs:
            tree.heading(col, text=text)
            anchor = "center" if col in ("id", "due") else "w"
            tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)
        tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        return tree

    def refresh(self):
        self.tree_refresh()

    def tree_refresh(self):
        renters = {x["id"]: x["name"] for x in database.list_renters()}
        self.active_tree.delete(*self.active_tree.get_children())
        for r in database.list_rentals(active_only=True):
            renter_name = renters.get(r["renter_id"], "-")
            self.active_tree.insert(
                "",
                "end",
                iid=str(r["id"]),
                values=(
                    r["equipment_name"],
                    r["user_id"],
                    renter_name,
                    r["rent_date"],
                    r["due_date"] or "-",
                ),
            )
        self.history_tree.delete(*self.history_tree.get_children())
        for r in database.list_rentals(active_only=False):
            self.history_tree.insert(
                "",
                "end",
                iid=f"h{r['id']}",
                values=(
                    r["id"],
                    r["equipment_name"],
                    r["user_id"],
                    r["rent_date"],
                    r["due_date"] or "-",
                    r["return_date"] or "미반납",
                ),
            )
