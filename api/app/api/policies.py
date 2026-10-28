# Central registry: a route being present does not authorize access.
PUBLIC_ROUTES = frozenset({("GET", "/health"), ("GET", "/livez")})
