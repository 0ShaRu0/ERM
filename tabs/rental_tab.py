import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk, messagebox

import database


class RentalTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self._equip_map = {}
        self._renter_map = {}
        self._build()
        self.refresh()

    def _build(self):
        rent_frame = ttk.LabelFrame(self, text="장비 대여", padding=8)
        rent_frame.pack(fill="x")

        ttk.Label(rent_frame, text="장비:").grid(row=0, column=0, sticky="w")
        self.equip_combo = ttk.Combobox(
            rent_frame, state="readonly", width=28
        )
        self.equip_combo.grid(row=0, column=1, padx=(4, 12))

        ttk.Label(rent_frame, text="대여자:").grid(row=0, column=2, sticky="w")
        self.renter_combo = ttk.Combobox(
            rent_frame, state="readonly", width=22
        )
        self.renter_combo.grid(row=0, column=3, padx=(4, 12))

        ttk.Label(rent_frame, text="반납 예정일:").grid(row=0, column=4, sticky="w")
        default_due = (date.today() + timedelta(days=7)).isoformat()
        self.due_var = tk.StringVar(value=default_due)
        ttk.Entry(rent_frame, textvariable=self.due_var, width=12).grid(
            row=0, column=5, padx=(4, 12)
        )
        ttk.Button(rent_frame, text="대여하기", command=self._rent).grid(
            row=0, column=6
        )

        inner = ttk.Notebook(self)
        inner.pack(fill="both", expand=True, pady=(10, 0))

        active_frame = ttk.Frame(inner, padding=6)
        history_frame = ttk.Frame(inner, padding=6)
        inner.add(active_frame, text=" 대여 현황 ")
        inner.add(history_frame, text=" 대여 이력 ")

        columns = ("id", "equip", "user", "name", "rent_date", "due")
        self.active_tree = self._make_tree(
            active_frame,
            columns,
            [
                ("id", "번호", 50),
                ("equip", "장비명", 160),
                ("user", "대여자 아이디", 110),
                ("name", "이름", 100),
                ("rent_date", "대여일시", 150),
                ("due", "반납 예정일", 100),
            ],
        )
        btns = ttk.Frame(active_frame)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="선택 장비 반납", command=self._return).pack(side="right")
        ttk.Button(
            btns,
            text="새로고침",
            command=lambda: self.app.user_refresh(self.refresh),
        ).pack(side="right", padx=(0, 6))

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

    def _make_tree(self, parent, columns, specs):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col, text, width in specs:
            tree.heading(col, text=text)
            anchor = "center" if col in ("id", "due") else "w"
            tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)
        tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        return tree

    def _rent(self):
        equip_sel = self.equip_combo.get()
        renter_sel = self.renter_combo.get()
        if equip_sel not in self._equip_map or renter_sel not in self._renter_map:
            messagebox.showwarning(
                "입력 확인", "장비와 대여자를 모두 선택하세요.", parent=self
            )
            return
        due = self.due_var.get().strip()
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                messagebox.showwarning(
                    "입력 확인", "반납 예정일은 YYYY-MM-DD 형식으로 입력하세요.", parent=self
                )
                return
        ok, msg = database.add_rental(
            self._equip_map[equip_sel], self._renter_map[renter_sel], due
        )
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _return(self):
        sel = self.active_tree.selection()
        if not sel:
            messagebox.showwarning("선택 확인", "반납할 항목을 선택하세요.", parent=self)
            return
        rental_id = int(sel[0])
        values = self.active_tree.item(sel[0], "values")
        if not messagebox.askyesno(
            "반납 확인", f"[{values[1]}] 장비를 반납 처리하시겠습니까?", parent=self
        ):
            return
        ok, msg = database.return_rental(rental_id)
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def refresh(self):
        self.tree_refresh()
        self.combo_refresh()

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
                    r["id"],
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

    def combo_refresh(self):
        current_equip = self.equip_combo.get()
        current_renter = self.renter_combo.get()

        self._equip_map.clear()
        for row in database.list_equipment():
            if row["available_count"] > 0:
                label = f"[{row['display_number']}] {row['name']}"
                if row["category"]:
                    label += f" ({row['category']})"
                label += (
                    f" - 대여 가능 {row['available_count']}/{row['quantity']}개"
                )
                self._equip_map[label] = row["id"]
        self.equip_combo.configure(values=list(self._equip_map))
        if current_equip in self._equip_map:
            self.equip_combo.set(current_equip)
        else:
            self.equip_combo.set("")

        self._renter_map.clear()
        for row in database.list_renters():
            label = f"{row['user_id']} - {row['name']}"
            self._renter_map[label] = row["id"]
        self.renter_combo.configure(values=list(self._renter_map))
        if current_renter in self._renter_map:
            self.renter_combo.set(current_renter)
        else:
            self.renter_combo.set("")
