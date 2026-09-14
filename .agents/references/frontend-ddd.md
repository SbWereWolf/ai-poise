# Frontend DDD and component ownership

Frontend code is subject to DDD and explicit dependency direction.

## Layers

| Layer                | Owns                                                                                           | Must not depend on                 |
|----------------------|------------------------------------------------------------------------------------------------|------------------------------------|
| Frontend Domain      | Client-owned deterministic product rules, value objects, policies                              | Vue, Pinia, Router, DOM, transport |
| Interaction Domain   | Reusable selection, table-operation, validation-target, error-presentation, and workflow rules | Concrete component implementation  |
| Frontend Application | Use cases, workflow coordination, ports, step transitions                                      | Concrete HTTP/browser adapter      |
| State                | Pinia stores exposing application actions/state                                                | DOM and presentation markup        |
| Presentation         | Vue SFC, props/emits/slots, local UI state, accessibility                                      | Backend persistence implementation |
| Infrastructure       | HTTP/host/WebSocket/storage/browser adapters                                                   | Presentation details               |

Reusable component behavior may depend on frontend/interaction-domain
rules; those rules must not depend on the consuming component. A
selectable table, add/delete policy, error navigator, or step workflow
can therefore be modeled as reusable domain/application logic and
consumed by multiple components.

Server-owned rights, limits, calculations, persistence decisions,
XML/XSD, and canonical business validation remain server-owned. The
frontend may represent server findings and enforce client-owned
structural/input rules, but it must not create a competing authority.

Every new or moved frontend symbol is classified in the existing Poise Task
content by layer, owner, allowed dependencies, and requirement/refactor IDs.
Use the owning content/trace APIs; do not introduce an ERP traceability registry.
