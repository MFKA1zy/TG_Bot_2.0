from .broadcast import router as broadcast_router
from .panel import router as panel_router
from .series import router as series_router
from .welcome import router as welcome_router
from .catalog import router as catalog_router
from .reviews import router as reviews_router
from .economy import router as economy_router
from .roulette import router as roulette_router

admin_router = panel_router
admin_router.include_router(series_router)
admin_router.include_router(welcome_router)
admin_router.include_router(broadcast_router)
admin_router.include_router(catalog_router)
admin_router.include_router(reviews_router)
admin_router.include_router(economy_router)
admin_router.include_router(roulette_router)

__all__ = ["admin_router"]
