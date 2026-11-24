"""Generate the beer site and serve the generated output locally."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from generate_site import OUTPUT_DIR, main as generate_site


def main() -> None:
    generate_site()

    output_dir = OUTPUT_DIR.resolve()
    handler = partial(SimpleHTTPRequestHandler, directory=str(output_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 8000), handler)
    print(f"Serving generated site at http://localhost:8000/ from {output_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
