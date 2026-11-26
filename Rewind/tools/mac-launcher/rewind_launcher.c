/* Rewind.app main executable: runs Contents/Resources/launcher.sh with /bin/bash. */
#include <mach-o/dyld.h>
#include <limits.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0) return 1;
    if (!realpath(exe, real)) strncpy(real, exe, sizeof(real));
    char *macos_dir = dirname(real);               /* .../Contents/MacOS */
    snprintf(script, sizeof(script), "%s/../Resources/launcher.sh", macos_dir);
    char *args[] = {"/bin/bash", script, NULL};
    execv("/bin/bash", args);
    perror("Rewind: could not start /bin/bash");
    return 1;
}
