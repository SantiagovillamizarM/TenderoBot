"""Un solo papel por correo también en los scripts y en las cuentas que crea el administrador."""

from types import SimpleNamespace

import pytest

from app import providers
from app.roles import other_role_error
from scripts import admin_user, seed


def fake_role(monkeypatch, module, role):
    async def email_role(session, email):
        return role

    monkeypatch.setattr(module, "email_role", email_role)


class FakeSession:
    def __init__(self, admins=(), others=0):
        self.admins = list(admins)
        self.others = others
        self.added, self.deleted = [], []

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def scalar(self, statement):
        # find_admin pide un administrador; delete_admin cuenta los demás.
        if "count" in str(statement).lower():
            return self.others
        return self.admins[0] if self.admins else None


@pytest.mark.parametrize(
    "role, wanted, blocked",
    [(None, "admin", False), ("admin", "admin", False), ("provider", "admin", True),
     ("tendero", "admin", True), ("admin", "provider", True), ("provider", "provider", False)],
)
def test_other_role_error(role, wanted, blocked):
    assert (other_role_error(role, wanted) is not None) is blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["provider", "tendero"])
async def test_admin_script_refuses_email_with_another_role(monkeypatch, role):
    fake_role(monkeypatch, admin_user, role)
    session = FakeSession()
    with pytest.raises(admin_user.AdminUserError, match="un solo papel"):
        await admin_user.save_admin(session, "x@correo.com", "hash")
    assert session.added == []


@pytest.mark.asyncio
async def test_admin_script_creates_or_updates(monkeypatch):
    fake_role(monkeypatch, admin_user, None)
    session = FakeSession()
    assert await admin_user.save_admin(session, "nuevo@correo.com", "h1") == "creada"
    assert session.added[0].email == "nuevo@correo.com"

    fake_role(monkeypatch, admin_user, "admin")
    existing = SimpleNamespace(id=1, email="yo@correo.com", password_hash="viejo")
    assert await admin_user.save_admin(FakeSession([existing]), "yo@correo.com", "h2") == "actualizada"
    assert existing.password_hash == "h2"


@pytest.mark.asyncio
async def test_delete_admin():
    existing = SimpleNamespace(id=1, email="yo@correo.com")
    session = FakeSession([existing], others=1)
    await admin_user.delete_admin(session, "yo@correo.com")
    assert session.deleted == [existing]
    with pytest.raises(admin_user.AdminUserError, match="única"):
        await admin_user.delete_admin(FakeSession([existing], others=0), "yo@correo.com")
    with pytest.raises(admin_user.AdminUserError, match="no es una cuenta"):
        await admin_user.delete_admin(FakeSession(), "otro@correo.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "tendero"])
async def test_admin_panel_cannot_give_provider_account_to_another_role(monkeypatch, role):
    fake_role(monkeypatch, providers, role)

    class Session(FakeSession):
        async def scalar(self, statement):
            return None  # no hay otra cuenta del panel con ese correo

    session = Session()
    with pytest.raises(providers.ProviderError, match="un solo papel"):
        await providers.add_user(session, 1, "x@correo.com")
    assert session.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "tendero"])
async def test_seed_refuses_panel_email_with_another_role(monkeypatch, role):
    fake_role(monkeypatch, seed, role)
    monkeypatch.setattr(seed, "get_settings", lambda: SimpleNamespace(panel_email="yo@correo.com"))
    session = FakeSession()
    with pytest.raises(seed.SeedError, match="PANEL_EMAIL"):
        await seed.seed(session)
    assert session.added == []
