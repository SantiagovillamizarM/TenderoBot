import pytest

from app.bot import handlers


@pytest.fixture(autouse=True)
def clean_start_choices():
    """Lo que cada chat escogió en /start vive en memoria: cada prueba empieza sin nada."""
    handlers._start_choice.clear()
    handlers._verified_phone.clear()
    yield
    handlers._start_choice.clear()
    handlers._verified_phone.clear()
