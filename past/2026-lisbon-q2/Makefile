generate: deps theme
	python _build/generate.py

deps:
	mkdir -p static

clean:
	rm -rf ./static/*

theme: deps  # waits for mkdir static: under make -j the theme copy raced it and css landed at the event root (SREday Campinas, 2026-11-20)
	cp -r ../_assets/template_v1/* static/
	cp -r assets static
	cp -r ../sponsors static

serve:
	python _build/serve.py

env:
	python3 -m venv env

.PHONY: generate serve theme clean
