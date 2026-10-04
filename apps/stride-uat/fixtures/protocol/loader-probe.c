/* Test-owned constructor: records real loader privilege, never alters it. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
__attribute__((constructor)) static void observe_identity(void) {
    const char *path = getenv("POISE_LOADER_TRACE");
    if (!path) return;
    FILE *out = fopen(path, "a");
    if (out) { fprintf(out, "%ld\n", (long)geteuid()); fclose(out); }
}
