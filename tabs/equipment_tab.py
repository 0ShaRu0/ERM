import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import database

try:
    from PIL import Image as pil_image
    from PIL import ImageTk as pil_image_tk
except ImportError:
    pil_image = None
    pil_image_tk = None

IMAGE_TYPES = [
    ("이미지 파일", "*.png *.jpg *.jpeg *.gif *.bmp"),
    ("모든 파일", "*.*"),
]


class EquipmentTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.image_src = None
        self._photo = None
        self._build()
        self.refresh()

    def _build(self):
        top = ttk.LabelFrame(self, text="장비 추가", padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="장비명:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var, width=20).grid(
            row=0, column=1, padx=(4, 12)
        )

        ttk.Label(top, text="분류:").grid(row=0, column=2, sticky="w")
        self.category_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.category_var, width=15).grid(
            row=0, column=3, padx=(4, 12)
        )

        ttk.Label(top, text="수량:").grid(row=0, column=4, sticky="w")
        self.quantity_var = tk.StringVar(value="1")
        ttk.Entry(top, textvariable=self.quantity_var, width=6).grid(
            row=0, column=5, padx=(4, 12)
        )

        self.image_label_var = tk.StringVar(value="선택된 이미지 없음")
        ttk.Button(top, text="추가", command=self._add).grid(row=0, column=6)
        ttk.Label(top, text="이미지:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(top, text="이미지 선택", command=self._pick_image).grid(
            row=1, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Label(top, textvariable=self.image_label_var, width=38).grid(
            row=1, column=2, columnspan=5, sticky="w", padx=(6, 0), pady=(8, 0)
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        search = ttk.Frame(body)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="검색:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var, width=25)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.refresh())
        ttk.Button(search, text="검색", command=self.refresh).pack(side="left")
        ttk.Button(search, text="삭제", command=self._delete).pack(side="right")

        columns = (
            "id", "name", "category", "quantity", "available", "status", "created_at"
        )
        table_frame = ttk.Frame(body)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        for col, text, width in [
            ("id", "번호", 50),
            ("name", "이름", 180),
            ("category", "분류", 110),
            ("quantity", "수량", 60),
            ("available", "대여 가능", 75),
            ("status", "상태", 90),
            ("created_at", "등록일", 150),
        ]:
            self.tree.heading(col, text=text)
            anchor = "center" if col in ("id", "quantity", "available", "status") else "w"
            self.tree.column(col, width=width, anchor=anchor)
        ysb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("busy", foreground="#c0392b")
        self.tree.tag_configure("ok", foreground="#27ae60")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_preview())

        preview_frame = ttk.LabelFrame(body, text="장비 이미지", padding=8)
        preview_frame.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        self.preview = ttk.Label(
            preview_frame, text="항목을 선택하세요", anchor="center"
        )
        self.preview.pack(fill="both", expand=True)

    def _pick_image(self):
        path = filedialog.askopenfilename(title="이미지 선택", filetypes=IMAGE_TYPES)
        if path:
            self.image_src = path
            filename = os.path.basename(path)
            if len(filename) > 38:
                filename = f"{filename[:35]}..."
            self.image_label_var.set(filename)

    def _add(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("입력 확인", "장비 이름을 입력하세요.", parent=self)
            return
        try:
            quantity = int(self.quantity_var.get().strip())
            if quantity < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "입력 확인", "수량은 1 이상의 숫자로 입력하세요.", parent=self
            )
            return
        database.add_equipment(
            name, self.category_var.get().strip(), quantity, self.image_src
        )
        self.name_var.set("")
        self.category_var.set("")
        self.quantity_var.set("1")
        self.image_src = None
        self.image_label_var.set("선택된 이미지 없음")
        self.app.refresh_all()

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("선택 확인", "삭제할 장비를 선택하세요.", parent=self)
            return
        eq = database.get_equipment(int(sel[0]))
        if not messagebox.askyesno(
            "삭제 확인", f"[{eq['name']}] 장비를 삭제하시겠습니까?", parent=self
        ):
            return
        ok, msg = database.delete_equipment(int(sel[0]))
        if ok:
            self.app.refresh_all()
        else:
            messagebox.showerror("실패", msg, parent=self)

    def _show_preview(self):
        sel = self.tree.selection()
        if not sel:
            self.preview.configure(text="항목을 선택하세요", image="")
            self._photo = None
            return
        eq = database.get_equipment(int(sel[0]))
        path = eq["image_path"]
        if not path or not os.path.exists(path):
            self.preview.configure(text="이미지 없음", image="")
            self._photo = None
            return
        if pil_image is None or pil_image_tk is None:
            self.preview.configure(text=f"{os.path.basename(path)}\n(미리보기는\npip install Pillow\n필요)", image="")
            return
        img = pil_image.open(path)
        img.thumbnail((240, 240))
        self._photo = pil_image_tk.PhotoImage(img)
        self.preview.configure(image=self._photo, text="")

    def refresh(self):
        keyword = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        self.tree.delete(*self.tree.get_children())
        for row in database.list_equipment(keyword):
            tag = "busy" if row["status"] == "대여중" else "ok"
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
                    row["created_at"],
                ),
                tags=(tag,),
            )
