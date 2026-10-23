from .broadcast import router as broadcast_router
from .panel import router as panel_router
from .series import router as series_router
from .welcome import router as welcome_router

admin_router = panel_router
admin_router.include_router(series_router)
admin_router.include_router(welcome_router)
admin_router.include_router(broadcast_router)

__all__ = ["admin_router"]
