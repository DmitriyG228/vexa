# ops/services — the runnable services of the ops domain

One directory per long-running service (the repo layout every domain shares). Today:

- [`help-mcp/`](help-mcp/) — the public help companion (MCP over FastAPI, port 8011): the
  "dynamic surface" users' coding agents connect to while deploying/integrating vexa.
