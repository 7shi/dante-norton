URL1 = https://www.gutenberg.org/ebooks/1995
URL2 = https://www.gutenberg.org/ebooks/1996
URL3 = https://www.gutenberg.org/ebooks/1997

TARGET1 = pg1995.txt
TARGET2 = pg1996.txt
TARGET3 = pg1997.txt

all: $(TARGET1) $(TARGET2) $(TARGET3)

$(TARGET1):
	wget --trust-server-names $(URL1).txt.utf-8

$(TARGET2):
	wget --trust-server-names $(URL2).txt.utf-8

$(TARGET3):
	wget --trust-server-names $(URL3).txt.utf-8

split: $(TARGET1) $(TARGET2) $(TARGET3)
	uv run split.py < $(TARGET1)
	uv run split.py < $(TARGET2)
	uv run split.py < $(TARGET3)

clean:
	rm -rf inferno purgatorio paradiso
