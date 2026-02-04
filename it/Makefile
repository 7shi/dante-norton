URL = https://www.gutenberg.org/ebooks/1000
TARGET = pg1000.txt

all: $(TARGET)

$(TARGET):
	wget --trust-server-names $(URL).txt.utf-8

split: $(TARGET)
	uv run split.py < $(TARGET)

clean:
	rm -rf inferno purgatorio paradiso
