import inspect

from app.main import app as app_instance


def test_worker_entrypoint_imports_the_fastapi_app():
    import worker

    assert worker.app is app_instance


def test_worker_entrypoint_exposes_an_async_on_fetch():
    import worker

    assert inspect.iscoroutinefunction(worker.on_fetch)
