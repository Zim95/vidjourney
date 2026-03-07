import logging
from functools import lru_cache
from typing import Any, Callable
import time
import inspect
from functools import wraps


@lru_cache(maxsize=1)
def get_logger():
    """
    Get the logger
    """
    logging.basicConfig(
        format="[%(asctime)s.%(msecs)03dZ] [%(name)s] [%(levelname)s] (%(filename)s:%(lineno)s) %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    log = logging.getLogger("VidJourney")
    log.setLevel(logging.INFO)
    return log


logger = get_logger()


def timer(_func: Callable | None = None, *, label: Callable[..., str] | str | None = None) -> Callable:
    """
    Timing decorator.

    Can be used as:
        @timer
        def f(...): ...

        @timer(label="my-step")
        def g(...): ...
    """

    def _decorate(func: Callable) -> Callable:
        def resolve_label(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            # If label is a callable, use it to build a dynamic label from call args
            if callable(label):
                try:
                    return str(label(*args, **kwargs))
                except Exception:
                    # Fallback to function name if label callable itself fails
                    return func.__qualname__
            # Otherwise treat label as a static string or default to qualname
            return str(label) if label is not None else func.__qualname__

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start: float = time.time()
                try:
                    return await func(*args, **kwargs)
                finally:
                    end: float = time.time()
                    name: str = resolve_label(args, kwargs)
                    logger.info(f"Time taken [{name}]: {end - start:.3f}s")

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start: float = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                end: float = time.time()
                name: str = resolve_label(args, kwargs)
                logger.info(f"Time taken [{name}]: {end - start:.3f}s")

        return sync_wrapper

    # Support both @timer and @timer(label="...")
    if _func is None:
        return _decorate
    else:
        return _decorate(_func)
