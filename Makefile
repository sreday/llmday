# Events: upcoming and running ones at the top, finished ones in past/ (make archive moves them, Marek 2026-10-17).
# Every event's own build reads the repo root through ../ (home/metadata.yml, about.yaml, sponsors, sibling events...),
# so all events and home are built side by side in .build/ - hard links of their folders (no extra disk) plus links to
# the shared root items. Nothing inside an event folder depends on where it lives, a past event keeps every feature
# (edit it in past/, it is rebuilt on every deploy) and the URLs never change: the output still goes to static/<event>/
events := $(wildcard 20*) $(notdir $(wildcard past/20*))

generate:
	@$(MAKE) --no-print-directory stage
	@$(MAKE) --no-print-directory site
	# Serve the files as they are: no Jekyll pass on GitHub Pages (it took ~4 min per deploy)
	touch static/.nojekyll

site: $(addprefix static/,$(addsuffix /index.html,$(events))) static/index.html

# .build/ = every event folder (top and past/) and home, hard-linked, their static/ left out; everything else at the
# root linked in as it is. sponsors/ is hard-linked too: event Makefiles run "cp -r ../sponsors static", which would
# copy a link as a link. An event both at the top and in past/ stops the build: an upload landed in the old folder.
stage:
	@for d in past/20*; do \
		[ -d "$$d" ] || continue; \
		if [ -d "$${d#past/}" ]; then echo "$${d#past/} is both at the top and in past/ - move its new files into $$d"; exit 1; fi; \
	done
	@mkdir -p .build static .cache
	@for d in 20* past/20* home sponsors; do \
		[ -d "$$d" ] || continue; \
		rsync -a --delete --exclude /static/ --link-dest="$(CURDIR)/$$d/" "$$d/" ".build/$$(basename $$d)/"; \
	done
	@for e in .build/20*; do \
		[ -d "$$e" ] || continue; \
		n=$$(basename $$e); [ -d "$$n" ] || [ -d "past/$$n" ] || rm -rf "$$e"; \
	done
	@for f in $$(ls -A); do \
		case "$$f" in 20*|past|home|sponsors|static|.build) continue;; esac; \
		ln -sfn "../$$f" ".build/$$f"; \
	done
	@ls past 2>/dev/null | grep '^20' > .build/.past-events || true

.build/20%/static/index.html: .build/20%/_build/* .build/20%/_templates/* .build/20%/_db/* .build/20%/Makefile .build/20%/metadata.*
	@echo $@
	cd .build/20$* && \
		make clean && \
		make generate

static/20%/index.html: .build/20%/static/index.html
	@echo ">>>" $@
	cp -r .build/20$*/static/ static/20$*
	cp .build/20$*/metadata.yml static/20$*
	cp .build/20$*/_db/talks.csv static/20$*

static/index.html: .build/home/**/*
	cd .build/home && \
		make clean && \
		make generate && \
		cp -r static/* ../../static && \
		cp metadata.yml ../../static
	cp -r photos ./static
	cp -r speakers ./static
	cp -r ambassadors ./static
	cp -r sponsors ./static

# finished top-level events -> past/ (git mv); DRY=1 only lists them
archive:
	python3 _build/archive_events.py $(if $(DRY),--dry-run)

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/* ./20*/static/* ./past/20*/static/* ./.build/*/static/*

serve:
	python3 _build/serve.py
	open http://localhost:8080

optimize:
	python _build/optimize_images.py

.PHONY: env deps clean serve optimize stage site archive
