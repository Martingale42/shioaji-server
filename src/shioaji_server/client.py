import shioaji as sj
from dataclasses import dataclass, field


@dataclass
class ShioajiClient:
    """Wraps a single Shioaji SDK instance."""

    api: sj.Shioaji = field(default_factory=sj.Shioaji)
    connected: bool = False
    simulation: bool = False

    def login(
        self,
        api_key: str,
        secret_key: str,
        ca_path: str | None = None,
        ca_passwd: str | None = None,
        simulation: bool = False,
    ) -> list[dict]:
        """Login and optionally activate CA certificate."""
        if self.connected:
            raise RuntimeError("Already connected")

        self.simulation = simulation
        if simulation:
            self.api = sj.Shioaji(simulation=True)

        accounts = self.api.login(api_key=api_key, secret_key=secret_key)

        if ca_path and ca_passwd:
            self.api.activate_ca(ca_path=ca_path, ca_passwd=ca_passwd)

        self.connected = True
        return [
            {"account_type": str(type(a).__name__), "account_id": a.account_id}
            for a in accounts
        ]

    def logout(self) -> None:
        if self.connected:
            self.api.logout()
            self.connected = False

    def require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("Not connected — call /api/auth/login first")
