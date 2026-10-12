"""An in-memory, two-device rendezvous used only while the USB tool is open."""
import secrets
import threading
import time


class UsbBroker:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.round = None
        self.submissions = {}
        self.acks = set()
        self.stage = "waiting"
        self.error = ""
        self.started = time.monotonic()

    def _status(self, client_id):
        own = next((item for item in self.submissions.values() if item["client_id"] == client_id), None)
        if not own:
            raise ValueError("이 동기화에 참여하지 않은 기기입니다.")
        result = {"round": self.round, "stage": self.stage, "error": self.error}
        if self.stage == "ready":
            result["pc"] = self.submissions["pc"]["backup"]
            result["phone"] = self.submissions["phone"]["backup"]
            result["preference"] = self.submissions["pc"]["preference"]
        return result

    def submit(self, body):
        role, client_id, backup = body.get("role"), body.get("client_id"), body.get("backup")
        if role not in ("pc", "phone") or not isinstance(client_id, str) or not client_id:
            raise ValueError("기기 종류와 ID를 확인하세요.")
        if not isinstance(backup, dict) or backup.get("app") != "erm-independent" or not isinstance(backup.get("data"), dict):
            raise ValueError("독립 앱의 동기화 데이터가 아닙니다.")
        preference = body.get("preference", "")
        if preference not in ("", "pc", "phone"):
            raise ValueError("충돌 처리 방식을 확인하세요.")
        with self.lock:
            if self.stage == "ready":
                self.stage = "failed"
                self.error = "진행 중인 동기화에 다시 참여했습니다. 양쪽에서 다시 시도하세요."
                raise ValueError(self.error)
            if self.stage in ("complete", "failed") or time.monotonic() - self.started > 300:
                self.submissions, self.acks, self.error = {}, set(), ""
                self.stage, self.round, self.started = "waiting", None, time.monotonic()
            if not self.round:
                self.round = secrets.token_urlsafe(16)
            other_role = "phone" if role == "pc" else "pc"
            if self.submissions.get(other_role, {}).get("client_id") == client_id:
                raise ValueError("같은 기기에서 두 역할로 참여할 수 없습니다.")
            if role in self.submissions and self.submissions[role]["client_id"] != client_id:
                raise ValueError("같은 역할의 다른 기기가 이미 참여했습니다. 설치 도구를 다시 실행하세요.")
            if self.stage == "waiting":
                self.submissions[role] = {"client_id": client_id, "backup": backup, "preference": preference}
            if len(self.submissions) == 2:
                self.stage = "ready"
            return self._status(client_id)

    def status(self, round_id, client_id):
        with self.lock:
            if round_id != self.round:
                raise ValueError("새 동기화가 시작되었습니다. 양쪽에서 다시 시도하세요.")
            return self._status(client_id)

    def acknowledge(self, body):
        with self.lock:
            if body.get("round") != self.round:
                raise ValueError("이전 동기화의 응답입니다. 양쪽에서 다시 시도하세요.")
            client_id = body.get("client_id")
            self._status(client_id)
            if body.get("ok") is not True:
                self.stage = "failed"
                self.error = str(body.get("error", "동기화를 중단했습니다."))[:2000]
            elif self.stage != "failed":
                if self.stage not in ("ready", "complete"):
                    raise ValueError("양쪽 데이터가 아직 준비되지 않았습니다.")
                self.acks.add(client_id)
                if len(self.acks) == 2:
                    self.stage = "complete"
                    # Keep only participant IDs after acknowledgement, not their data.
                    for item in self.submissions.values():
                        item["backup"] = None
            return self._status(client_id)
