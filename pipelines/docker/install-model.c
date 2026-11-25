/* Entrypoint for the model init container. The destination is a mounted emptyDir. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const char *files[] = {"model.joblib", "feature_schema.json", "metrics.json", "evaluation.json"};
    if (argc != 2 || chdir(argv[1]) != 0) {
        fprintf(stderr, "Supply a writable destination directory\n");
        return EXIT_FAILURE;
    }
    for (size_t i = 0; i < sizeof(files) / sizeof(files[0]); ++i) {
        char path[80], buffer[65536];
        snprintf(path, sizeof(path), "/model/%s", files[i]);
        FILE *src = fopen(path, "rb");
        FILE *dst = fopen(files[i], "wb");
        if (!src || !dst) { perror(files[i]); return EXIT_FAILURE; }
        size_t count;
        while ((count = fread(buffer, 1, sizeof(buffer), src)) > 0) {
            if (fwrite(buffer, 1, count, dst) != count) { perror(files[i]); return EXIT_FAILURE; }
        }
        if (ferror(src) || fclose(dst) != 0) { perror(files[i]); return EXIT_FAILURE; }
        fclose(src);
    }
    return EXIT_SUCCESS;
}
