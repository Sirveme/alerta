"""Instancia única de Jinja2Templates con globals y filtros registrados."""
from fastapi.templating import Jinja2Templates

from app.core.assets import static_url
from app.core.timezone import fmt_lima, fmt_lima_date, fmt_lima_time

templates = Jinja2Templates(directory="templates")

# Globals
templates.env.globals["static_url"] = static_url

# Filtros
templates.env.filters["to_lima_time"] = fmt_lima
templates.env.filters["to_lima_date"] = fmt_lima_date
templates.env.filters["to_lima_hour"] = fmt_lima_time
