import sys, os, re
from dante_norton import roman_number

dir = ""
no = 0
text = ""

def write():
    global dir, no, text
    if dir:
        with open(f"{dir}/{no:02}.txt", "w") as file:
            file.write(text)
        text = ""

while (line := sys.stdin.readline()):
    line = line.rstrip()
    if re.match("[A-Z]+$", line):
        pass
    elif line in ["Inferno", "Purgatorio", "Paradiso"]:
        write()
        dir = line.lower()
        if not os.path.exists(dir):
            os.mkdir(dir)
        line = sys.stdin.readline().rstrip()
        if not (m:= re.match(r"Canto (\w+)", line)):
            raise ValueError(f"invalid line: {line}")
        no = roman_number(m.group(1))
    elif line.lstrip().startswith("*** END"):
        write()
        break
    elif line and dir:
        text += line.lstrip() + "\n"
