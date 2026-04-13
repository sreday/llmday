generate: deps theme
	python _build/generate.py

deps:
	mkdir -p static

clean:
	rm -rf ./static/*

theme:
	cp -r template/assets static/assets
	cp -r template/favicon.ico static
	cp -r assets static
	mkdir -p static/videos && cp -r ../videos/* static/videos/ 2>/dev/null || true

serve:
	python _build/serve.py

env:
	python3 -m venv env

.PHONY: generate serve theme clean
