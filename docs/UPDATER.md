# Repository-owned Controller distribution

Controller owns and publishes its library wheel/source in this repository's v1.0.2 Release. The publication workflow builds this package only. It is architecture-independent Python source; binary dependencies are resolved by the runnable application's build.

Generation MCP embeds the fixed Controller library and publishes its own complete Docker image and Updater installation catalog. Updater installs that process once; a separate Controller service/catalog would create another authority for the same domain and is deliberately not added. Studio's internal HTTP API and the external MCP facade share that one runtime.

The Owner SDK in this source supports advanced internal migration integration. It is not a prerequisite for the ordinary fresh installation recipe in Generation MCP. Publication and actual-host/provider acceptance are checked separately. No database, storage, credentials or runtime is changed by library publication.

