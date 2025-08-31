import reflex as rx

config = rx.Config(
    app_name="seezar_admin",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ],
)
