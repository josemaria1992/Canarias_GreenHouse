# ADR-0010 Controlled polling instead of WebSocket/SSE
Status: accepted (allowed by the brief)

Decision: the UI polls authenticated JSON endpoints once per second. Every request passes the same
session, origin and permission checks; session revocation takes effect on the next poll. A stale
banner appears after 4 s without a good response.
Impact: no separate stream authorization to get wrong; slightly more requests on a LAN.
