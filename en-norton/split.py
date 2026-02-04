import sys, os, re
from dante_norton import roman_number

# Section markers and their directory names
SECTIONS = {
    "HELL.": "inferno",
    "PURGATORY": "purgatorio",
    "PARADISE": "paradiso",
}

dir = ""
no = 0
text = ""

def write():
    global dir, no, text
    if dir:
        with open(f"{dir}/{no:02}.txt", "w") as file:
            file.write(text.lstrip("\n").rstrip())
        text = ""

while (line := sys.stdin.readline()):
    line = line.rstrip()
    if re.match("^[A-Z]+$", line) and line not in SECTIONS:
        pass
    elif line in SECTIONS:
        write()
        dir = SECTIONS[line]
        if not os.path.exists(dir):
            os.mkdir(dir)
    elif m := re.match(r"CANTO (\w+)\.$", line):
        write()
        no = roman_number(m.group(1))
    elif line.lstrip().startswith("*** END"):
        write()
        break
    elif dir:
        text += line.lstrip() + "\n"
