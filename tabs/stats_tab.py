import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

import database
import stats_export

BAR_COLORS = {"rent": "#4C72B0", "return": "#55A868"}


class StatsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self._all_data = None
        self._eq_data = None
        self._equipment_map = {}
        self._edit_equipment_map = {}
        self._selected_stat_key = None

        export_frame = ttk.LabelFrame(self, text="통계 파일 출력", padding=8)
        export_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(export_frame, text="출력 형식:").pack(side="left")
        self.export_format_var = tk.StringVar(value="PDF")
        self.export_format_combo = ttk.Combobox(
            export_frame,
            textvariable=self.export_format_var,
            values=("PDF", "Excel"),
            state="readonly",
            width=10,
        )
        self.export_format_combo.pack(side="left", padx=6)
        ttk.Button(
            export_frame, text="현재 통계 저장", command=self._export_statistics
        ).pack(side="left")
        ttk.Label(
            export_frame,
            text="현재 선택한 통계를 저장합니다.",
            foreground="#666666",
        ).pack(side="left", padx=(12, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        page_all = ttk.Frame(self.notebook, padding=8)
        page_eq = ttk.Frame(self.notebook, padding=8)
        page_edit = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(page_all, text=" 전체 통계 ")
        self.notebook.add(page_eq, text=" 장비별 통계 ")
        self.notebook.add(page_edit, text=" 통계 편집 ")

        self._build_all(page_all)
        self._build_equipment(page_eq)
        self._build_editor(page_edit)
        self.refresh()

    def _build_all(self, page):
        control = ttk.Frame(page)
        control.pack(fill="x")
        ttk.Label(control, text="년도 선택:").pack(side="left")
        self.year_var = tk.StringVar()
        self.year_combo = ttk.Combobox(
            control, textvariable=self.year_var, state="readonly", width=8
        )
        self.year_combo.pack(side="left", padx=6)
        self.year_combo.bind("<<ComboboxSelected>>", lambda e: self._load_monthly())
        ttk.Button(
            control,
            text="새로고침",
            command=lambda: self.app.user_refresh(self.refresh),
        ).pack(side="left")

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        monthly_frame = ttk.LabelFrame(body, text="월별 통계 (선택 년도)", padding=6)
        monthly_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        columns = ("month", "rent", "ret")
        self.month_tree = ttk.Treeview(
            monthly_frame, columns=columns, show="headings", height=12
        )
        for col, text in [
            ("month", "월"),
            ("rent", "대여 건수"),
            ("ret", "반납 건수"),
        ]:
            self.month_tree.heading(col, text=text)
            self.month_tree.column(col, width=70 if col == "month" else 90,
                                   anchor="center")
        ysb = ttk.Scrollbar(monthly_frame, orient="vertical",
                            command=self.month_tree.yview)
        self.month_tree.configure(yscrollcommand=ysb.set)
        self.month_tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        chart_frame = ttk.LabelFrame(body, text="월별 대여/반납 추이", padding=6)
        chart_frame.grid(row=0, column=1, sticky="nsew")
        self.canvas = tk.Canvas(chart_frame, background="white",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_all())

        yearly_frame = ttk.LabelFrame(body, text="년도별 요약", padding=6)
        yearly_frame.grid(row=1, column=0, columnspan=2, sticky="nsew",
                          pady=(8, 0))
        y_columns = ("year", "rent", "ret")
        self.year_tree = ttk.Treeview(
            yearly_frame, columns=y_columns, show="headings", height=5
        )
        for col, text in [
            ("year", "년도"),
            ("rent", "대여 건수"),
            ("ret", "반납 건수"),
        ]:
            self.year_tree.heading(col, text=text)
            self.year_tree.column(col, width=120, anchor="center")
        yysb = ttk.Scrollbar(yearly_frame, orient="vertical",
                             command=self.year_tree.yview)
        self.year_tree.configure(yscrollcommand=yysb.set)
        self.year_tree.pack(side="left", fill="both", expand=True)
        yysb.pack(side="right", fill="y")

    def _build_equipment(self, page):
        control = ttk.Frame(page)
        control.pack(fill="x")
        ttk.Label(control, text="장비 선택:").pack(side="left")
        self.eq_var = tk.StringVar()
        self.eq_combo = ttk.Combobox(
            control, textvariable=self.eq_var, state="readonly", width=26
        )
        self.eq_combo.pack(side="left", padx=(4, 14))
        self.eq_combo.bind("<<ComboboxSelected>>", lambda e: self._load_eq_monthly())

        ttk.Label(control, text="년도 선택:").pack(side="left")
        self.eq_year_var = tk.StringVar()
        self.eq_year_combo = ttk.Combobox(
            control, textvariable=self.eq_year_var, state="readonly", width=8
        )
        self.eq_year_combo.pack(side="left", padx=6)
        self.eq_year_combo.bind("<<ComboboxSelected>>",
                                lambda e: self._load_eq_monthly())

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        monthly_frame = ttk.LabelFrame(
            body, text="월별 통계 (선택 장비 + 년도)", padding=6
        )
        monthly_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        columns = ("month", "rent", "ret")
        self.eq_month_tree = ttk.Treeview(
            monthly_frame, columns=columns, show="headings", height=12
        )
        for col, text in [
            ("month", "월"),
            ("rent", "대여 건수"),
            ("ret", "반납 건수"),
        ]:
            self.eq_month_tree.heading(col, text=text)
            self.eq_month_tree.column(col, width=70 if col == "month" else 90,
                                      anchor="center")
        eq_ysb = ttk.Scrollbar(monthly_frame, orient="vertical",
                               command=self.eq_month_tree.yview)
        self.eq_month_tree.configure(yscrollcommand=eq_ysb.set)
        self.eq_month_tree.pack(side="left", fill="both", expand=True)
        eq_ysb.pack(side="right", fill="y")

        chart_frame = ttk.LabelFrame(body, text="월별 대여/반납 추이", padding=6)
        chart_frame.grid(row=0, column=1, sticky="nsew")
        self.eq_canvas = tk.Canvas(chart_frame, background="white",
                                   highlightthickness=0)
        self.eq_canvas.pack(fill="both", expand=True)
        self.eq_canvas.bind("<Configure>", lambda e: self._redraw_eq())

        rank_frame = ttk.LabelFrame(
            body, text="장비별 누적 대여 현황 (항목 클릭 시 상세 조회)", padding=6
        )
        rank_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        r_columns = ("name", "total", "returned", "active", "last")
        self.rank_tree = ttk.Treeview(
            rank_frame, columns=r_columns, show="headings", height=6
        )
        for col, text, width in [
            ("name", "장비명", 180),
            ("total", "누적 대여", 80),
            ("returned", "반납 완료", 80),
            ("active", "대여 중", 70),
            ("last", "최근 대여일", 150),
        ]:
            self.rank_tree.heading(col, text=text)
            anchor = "center" if col != "name" and col != "last" else "w"
            self.rank_tree.column(col, width=width, anchor=anchor)
        r_ysb = ttk.Scrollbar(rank_frame, orient="vertical",
                              command=self.rank_tree.yview)
        self.rank_tree.configure(yscrollcommand=r_ysb.set)
        self.rank_tree.pack(side="left", fill="both", expand=True)
        r_ysb.pack(side="right", fill="y")
        self.rank_tree.bind("<<TreeviewSelect>>", self._on_rank_select)

    def _build_editor(self, page):
        form = ttk.LabelFrame(page, text="통계 편집", padding=8)
        form.pack(fill="x")

        ttk.Label(form, text="장비:").grid(row=0, column=0, sticky="w")
        self.edit_equipment_var = tk.StringVar()
        self.edit_equipment_combo = ttk.Combobox(
            form, textvariable=self.edit_equipment_var, state="readonly", width=26
        )
        self.edit_equipment_combo.grid(row=0, column=1, padx=(4, 10), sticky="w")

        ttk.Label(form, text="통계 월:").grid(row=0, column=2, sticky="w")
        self.stat_month_var = tk.StringVar(value=date.today().isoformat()[:7])
        ttk.Entry(form, textvariable=self.stat_month_var, width=9).grid(
            row=0, column=3, padx=(4, 10), sticky="w"
        )

        ttk.Label(form, text="대여:").grid(row=0, column=4, sticky="w")
        self.stat_rent_var = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.stat_rent_var, width=6).grid(
            row=0, column=5, padx=(4, 10), sticky="w"
        )

        ttk.Label(form, text="반납:").grid(row=0, column=6, sticky="w")
        self.stat_return_var = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.stat_return_var, width=6).grid(
            row=0, column=7, padx=(4, 0), sticky="w"
        )

        actions = ttk.Frame(form)
        actions.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="추가", command=self._add_override).pack(side="left")
        ttk.Button(actions, text="변경 저장", command=self._save_override).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(actions, text="제거", command=self._remove_override).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(actions, text="초기화", command=self._reset_overrides).pack(
            side="left", padx=(6, 0)
        )

        ttk.Label(
            form,
            text=(
                "실제 대여 이력은 보존됩니다. 초기화하면 수정/제거 표시를 지우고 "
                "자동 통계로 복원합니다."
            ),
            foreground="#555555",
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(8, 0))

        list_frame = ttk.LabelFrame(page, text="월별 장비 통계", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("equip", "month", "rent", "return", "status")
        self.override_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        for col, text, width in [
            ("equip", "장비", 260),
            ("month", "통계 월", 90),
            ("rent", "대여", 75),
            ("return", "반납", 75),
            ("status", "상태", 80),
        ]:
            self.override_tree.heading(col, text=text)
            anchor = "center" if col in ("month", "rent", "return", "status") else "w"
            self.override_tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(list_frame, orient="vertical", command=self.override_tree.yview)
        self.override_tree.configure(yscrollcommand=ysb.set)
        self.override_tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.override_tree.bind("<<TreeviewSelect>>", self._on_override_select)

    def refresh(self):
        years = database.stat_years()
        current = str(date.today().year)
        all_years = sorted(set(years) | {current}, reverse=True)

        selected = self.year_var.get()
        if selected not in all_years:
            selected = all_years[0]
        self.year_combo.configure(values=all_years)
        self.year_var.set(selected)

        eq_selected = self.eq_year_var.get()
        if eq_selected not in all_years:
            eq_selected = all_years[0]
        self.eq_year_combo.configure(values=all_years)
        self.eq_year_var.set(eq_selected)

        selected_item = self._equipment_map.get(self.eq_var.get())
        selected_id = selected_item[0] if selected_item else None
        items = database.equipment_stat_items()
        self._equipment_map = {
            self._equipment_label(item):
                (item["equipment_id"], item["equipment_name"])
            for item in items
        }
        labels = list(self._equipment_map)
        self.eq_combo.configure(values=labels)
        selected_label = next(
            (
                label
                for label, (equipment_id, _) in self._equipment_map.items()
                if equipment_id == selected_id
            ),
            labels[0] if labels else "",
        )
        self.eq_var.set(selected_label)

        self._load_yearly()
        self._load_monthly()
        self._refresh_ranking()
        self._load_eq_monthly()
        self._refresh_editor()

    def _refresh_editor(self):
        selected_id = None
        if self.edit_equipment_var.get() in self._edit_equipment_map:
            selected_id = self._edit_equipment_map[self.edit_equipment_var.get()]

        items = database.equipment_stat_items()
        self._edit_equipment_map = {
            self._equipment_label(item): item["equipment_id"] for item in items
        }
        labels = list(self._edit_equipment_map)
        self.edit_equipment_combo.configure(values=labels)
        selected_label = next(
            (
                label
                for label, equipment_id in self._edit_equipment_map.items()
                if equipment_id == selected_id
            ),
            labels[0] if labels else "",
        )
        if self._selected_stat_key is None:
            self.edit_equipment_var.set(selected_label)

        selected_key = self._selected_stat_key
        self.override_tree.delete(*self.override_tree.get_children())
        for row in database.list_effective_monthly_stats():
            item_id = self._stat_iid(row["equipment_id"], row["stat_month"])
            label = self._equipment_label(row)
            self.override_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    label,
                    row["stat_month"],
                    row["rent_count"],
                    row["return_count"],
                    row["source_status"],
                ),
            )
            self._edit_equipment_map[label] = row["equipment_id"]
        if selected_key is None:
            return
        selected_iid = self._stat_iid(*selected_key)
        if selected_iid in self.override_tree.get_children():
            self.override_tree.selection_set(selected_iid)
            self.override_tree.see(selected_iid)
        else:
            self._selected_stat_key = None

    def _on_override_select(self, _event=None):
        sel = self.override_tree.selection()
        if not sel:
            return
        equipment_id, stat_month = self._parse_stat_iid(sel[0])
        values = self.override_tree.item(sel[0], "values")
        self._selected_stat_key = (equipment_id, stat_month)
        self.edit_equipment_var.set(values[0])
        self.stat_month_var.set(values[1])
        self.stat_rent_var.set(str(values[2]))
        self.stat_return_var.set(str(values[3]))

    def _stat_iid(self, equipment_id, stat_month):
        return f"{equipment_id}:{stat_month}"

    def _parse_stat_iid(self, item_id):
        equipment_id, stat_month = item_id.split(":", 1)
        return int(equipment_id), stat_month

    def _override_input(self, require_positive=False):
        label = self.edit_equipment_var.get()
        if label not in self._edit_equipment_map:
            messagebox.showwarning("입력 확인", "장비를 선택하세요.", parent=self)
            return None

        stat_month = self.stat_month_var.get().strip()
        try:
            parsed_month = date.fromisoformat(f"{stat_month}-01")
            if parsed_month.isoformat()[:7] != stat_month:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "입력 확인", "통계 월은 YYYY-MM 형식으로 입력하세요.", parent=self
            )
            return None

        try:
            rent_count = int(self.stat_rent_var.get().strip())
            return_count = int(self.stat_return_var.get().strip())
            if rent_count < 0 or return_count < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "입력 확인", "대여와 반납 건수는 0 이상의 숫자로 입력하세요.", parent=self
            )
            return None

        if require_positive and rent_count == 0 and return_count == 0:
            messagebox.showwarning(
                "입력 확인", "대여 또는 반납 건수를 1건 이상 입력하세요.", parent=self
            )
            return None

        return self._edit_equipment_map[label], stat_month, rent_count, return_count

    def _add_override(self):
        override = self._override_input(require_positive=True)
        if override is None:
            return
        equipment_id, stat_month, rent_count, return_count = override
        ok, msg = database.add_stat_override(
            equipment_id, stat_month, rent_count, return_count
        )
        if ok:
            self._selected_stat_key = (equipment_id, stat_month)
            self.app.refresh_all()
        else:
            messagebox.showwarning("입력 확인", msg, parent=self)

    def _save_override(self):
        if self._selected_stat_key is None:
            messagebox.showwarning("선택 확인", "변경할 통계를 선택하세요.", parent=self)
            return
        override = self._override_input()
        if override is None:
            return
        selected_id, selected_month = self._selected_stat_key
        equipment_id, stat_month, rent_count, return_count = override
        if (equipment_id, stat_month) != (selected_id, selected_month):
            messagebox.showwarning(
                "입력 확인",
                "선택한 통계의 장비와 통계 월은 변경할 수 없습니다. 새 월은 추가를 사용하세요.",
                parent=self,
            )
            return
        ok, msg = database.save_stat_override(
            equipment_id, stat_month, rent_count, return_count
        )
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _remove_override(self):
        if self._selected_stat_key is None:
            messagebox.showwarning("선택 확인", "제거할 통계를 선택하세요.", parent=self)
            return
        values = self.override_tree.item(
            self._stat_iid(*self._selected_stat_key), "values"
        )
        if not messagebox.askyesno(
            "제거 확인",
            f"선택한 통계를 제거하시겠습니까?\n장비: {values[0]}\n통계 월: {values[1]}",
            parent=self,
        ):
            return
        ok, msg = database.remove_stat_override(*self._selected_stat_key)
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _reset_overrides(self):
        if not messagebox.askyesno(
            "초기화 확인",
            "모든 통계 수정을 초기화하시겠습니까?\n실제 대여 이력은 유지되고 자동 통계로 복원됩니다.",
            parent=self,
        ):
            return
        database.reset_stat_overrides()
        self._selected_stat_key = None
        self.app.refresh_all()

    def _load_yearly(self):
        self.year_tree.delete(*self.year_tree.get_children())
        for year, rent_cnt, ret_cnt in database.yearly_stats():
            self.year_tree.insert("", "end", values=(year, rent_cnt, ret_cnt))

    def _load_monthly(self):
        year = self.year_var.get()
        data = database.monthly_stats(year)
        self.month_tree.delete(*self.month_tree.get_children())
        total_rent = total_ret = 0
        for month, rent_cnt, ret_cnt in data:
            total_rent += rent_cnt
            total_ret += ret_cnt
            self.month_tree.insert("", "end",
                                   values=(f"{month}월", rent_cnt, ret_cnt))
        self.month_tree.insert("", "end", values=("합계", total_rent, total_ret))
        self._all_data = data
        self._draw_chart(self.canvas, data)

    def _refresh_ranking(self):
        self.rank_tree.delete(*self.rank_tree.get_children())
        for row in database.equipment_totals():
            active = row["active_count"] or 0
            label = self._equipment_label(row)
            self.rank_tree.insert(
                "",
                "end",
                iid=str(row["equipment_id"]),
                values=(
                    label,
                    row["rent_count"],
                    row["return_count"],
                    active,
                    row["last_rent"],
                ),
            )

    def _on_rank_select(self, _event=None):
        sel = self.rank_tree.selection()
        if not sel:
            return
        equipment_id = int(sel[0])
        label = next(
            (
                item_label
                for item_label, (item_id, _) in self._equipment_map.items()
                if item_id == equipment_id
            ),
            None,
        )
        if label:
            self.eq_var.set(label)
            self._load_eq_monthly()

    def _load_eq_monthly(self):
        selected = self._equipment_map.get(self.eq_var.get())
        year = self.eq_year_var.get()
        self.eq_month_tree.delete(*self.eq_month_tree.get_children())
        self._eq_data = None
        if not selected or not year:
            return
        equipment_id, _ = selected
        data = database.monthly_stats_by_equipment(year, equipment_id)
        total_rent = total_ret = 0
        for month, rent_cnt, ret_cnt in data:
            total_rent += rent_cnt
            total_ret += ret_cnt
            self.eq_month_tree.insert("", "end",
                                      values=(f"{month}월", rent_cnt, ret_cnt))
        self.eq_month_tree.insert("", "end",
                                  values=("합계", total_rent, total_ret))
        self._eq_data = data
        self._draw_chart(self.eq_canvas, data)

    def _redraw_all(self):
        if self._all_data:
            self._draw_chart(self.canvas, self._all_data)

    def _redraw_eq(self):
        if self._eq_data:
            self._draw_chart(self.eq_canvas, self._eq_data)

    def _export_statistics(self):
        if self.notebook.index("current") == 1:
            export_data = self._equipment_export_data()
            if export_data is None:
                return
            title, details, tables, filename = export_data
        else:
            title, details, tables, filename = self._all_export_data()

        file_format = self.export_format_var.get()
        if file_format == "PDF":
            extension = ".pdf"
            filetypes = [("PDF 파일", "*.pdf")]
            exporter = stats_export.export_pdf
        else:
            extension = ".xlsx"
            filetypes = [("Excel 파일", "*.xlsx")]
            exporter = stats_export.export_excel

        path = filedialog.asksaveasfilename(
            parent=self,
            title="통계 파일 저장",
            initialfile=f"{filename}{extension}",
            defaultextension=extension,
            filetypes=filetypes,
        )
        if not path:
            return

        try:
            exporter(path, title, details, tables)
        except ImportError as exc:
            messagebox.showerror(
                "출력 실패",
                f"필요한 라이브러리가 설치되지 않았습니다.\n{exc}",
                parent=self,
            )
            return
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("출력 실패", str(exc), parent=self)
            return

    def _all_export_data(self):
        year = self.year_var.get()
        monthly = database.monthly_stats(year)
        total_rent = sum(row[1] for row in monthly)
        total_return = sum(row[2] for row in monthly)
        monthly_rows = [
            (f"{month}월", rent_count, return_count)
            for month, rent_count, return_count in monthly
        ]
        monthly_rows.append(("합계", total_rent, total_return))

        ranking_rows = []
        for row in database.equipment_totals():
            active = row["active_count"] or 0
            ranking_rows.append(
                (
                    self._equipment_label(row),
                    row["rent_count"],
                    row["return_count"],
                    active,
                    row["last_rent"] or "-",
                )
            )

        tables = [
            (
                f"월별 통계 ({year}년)",
                ("월", "대여 건수", "반납 건수"),
                monthly_rows,
            ),
            (
                "년도별 요약",
                ("년도", "대여 건수", "반납 건수"),
                database.yearly_stats(),
            ),
            (
                "장비별 누적 대여 현황",
                ("장비명", "누적 대여", "반납 완료", "대여 중", "최근 대여일"),
                ranking_rows,
            ),
        ]
        details = (("기준 연도", f"{year}년"), ("출력일", date.today().isoformat()))
        return "장비 대여 전체 통계", details, tables, f"전체_통계_{year}"

    def _equipment_export_data(self):
        selected = self._equipment_map.get(self.eq_var.get())
        year = self.eq_year_var.get()
        if not selected or not year:
            messagebox.showwarning(
                "선택 확인", "출력할 장비와 연도를 선택하세요.", parent=self
            )
            return None

        equipment_id, name = selected
        monthly = database.monthly_stats_by_equipment(year, equipment_id)
        total_rent = sum(row[1] for row in monthly)
        total_return = sum(row[2] for row in monthly)
        rows = [
            (f"{month}월", rent_count, return_count)
            for month, rent_count, return_count in monthly
        ]
        rows.append(("합계", total_rent, total_return))
        details = (
            ("장비명", name),
            ("기준 연도", f"{year}년"),
            ("출력일", date.today().isoformat()),
        )
        tables = [
            (
                f"월별 통계 ({year}년)",
                ("월", "대여 건수", "반납 건수"),
                rows,
            )
        ]
        safe_name = "".join(
            "_" if character in '<>:"/\\|?*' else character for character in name
        ).strip(". ")
        return f"{name} 장비 대여 통계", details, tables, f"{safe_name}_통계_{year}"

    def _equipment_label(self, item):
        if item["is_current"]:
            return f"[{item['display_number']}] {item['equipment_name']}"
        return f"[삭제됨 #{item['equipment_id']}] {item['equipment_name']}"

    def _draw_chart(self, canvas, data):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 260)
        margin_l, margin_r, margin_t, margin_b = 45, 15, 35, 35
        plot_w = width - margin_l - margin_r
        plot_h = height - margin_t - margin_b
        max_count = max(max(r for _, r, _ in data), max(t for _, _, t in data), 1)
        scale = plot_h / max_count

        baseline_y = height - margin_b
        canvas.create_line(margin_l, margin_t - 10, margin_l, baseline_y,
                           fill="#555555")
        canvas.create_line(margin_l, baseline_y, width - margin_r, baseline_y,
                           fill="#555555")

        group_w = plot_w / len(data)
        bar_w = min(group_w * 0.34, 26)

        legend_x = margin_l + 5
        canvas.create_rectangle(legend_x, 6, legend_x + 12, 18,
                                fill=BAR_COLORS["rent"], outline="")
        canvas.create_text(legend_x + 16, 12, anchor="w", text="대여")
        canvas.create_rectangle(legend_x + 55, 6, legend_x + 67, 18,
                                fill=BAR_COLORS["return"], outline="")
        canvas.create_text(legend_x + 71, 12, anchor="w", text="반납")

        for i, (month, rent_cnt, ret_cnt) in enumerate(data):
            cx = margin_l + group_w * (i + 0.5)
            x1 = cx - bar_w - 1
            x2 = cx - 1
            y = baseline_y - rent_cnt * scale
            canvas.create_rectangle(x1, y, x2, baseline_y,
                                    fill=BAR_COLORS["rent"], outline="")
            if rent_cnt > 0:
                canvas.create_text(cx - bar_w / 2 - 1, y - 7, text=str(rent_cnt),
                                   font=("TkDefaultFont", 8))

            x1 = cx + 1
            x2 = cx + bar_w + 1
            y = baseline_y - ret_cnt * scale
            canvas.create_rectangle(x1, y, x2, baseline_y,
                                    fill=BAR_COLORS["return"], outline="")
            if ret_cnt > 0:
                canvas.create_text(cx + bar_w / 2 + 1, y - 7, text=str(ret_cnt),
                                   font=("TkDefaultFont", 8))

            canvas.create_text(cx, baseline_y + 12, text=f"{month}",
                               font=("TkDefaultFont", 9))
