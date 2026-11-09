from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = 'orders'

    def ready(self):
        # Register signal handlers (supplier shipment pre-creation).
        from . import signals  # noqa: F401
