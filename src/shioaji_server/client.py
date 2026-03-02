import asyncio
from dataclasses import dataclass, field

import shioaji as sj


@dataclass
class ShioajiClient:
    """Wraps a single Shioaji SDK instance."""

    api: sj.Shioaji = field(default_factory=sj.Shioaji)
    connected: bool = False
    simulation: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _login_sync(
        self,
        api_key: str,
        secret_key: str,
        ca_path: str | None = None,
        ca_passwd: str | None = None,
        simulation: bool = False,
    ) -> list[dict]:
        """Synchronous login — run via executor to avoid blocking the loop."""
        self.api = sj.Shioaji(simulation=simulation)

        accounts = self.api.login(api_key=api_key, secret_key=secret_key)

        if ca_path is not None and ca_passwd is not None:
            self.api.activate_ca(ca_path=ca_path, ca_passwd=ca_passwd)

        return [
            {"account_type": str(type(a).__name__), "account_id": a.account_id}
            for a in accounts
        ]

    async def login(
        self,
        api_key: str,
        secret_key: str,
        ca_path: str | None = None,
        ca_passwd: str | None = None,
        simulation: bool = False,
    ) -> list[dict]:
        """Login and optionally activate CA certificate."""
        async with self._lock:
            if self.connected:
                raise RuntimeError("Already connected")
            loop = asyncio.get_running_loop()
            accounts = await loop.run_in_executor(
                None,
                self._login_sync,
                api_key,
                secret_key,
                ca_path,
                ca_passwd,
                simulation,
            )
            self.connected = True
            self.simulation = simulation
            return accounts

    def _logout_sync(self) -> None:
        self.api.logout()

    async def logout(self) -> None:
        async with self._lock:
            if not self.connected:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._logout_sync)
            self.connected = False

    def require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("Not connected — call /api/auth/login first")
