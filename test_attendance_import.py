
from pathlib import Path

from academics.attendance_import import parse_attendance_excel

files = [
    "QT.xlsx",
    "PDC.xlsx",
]

for filename in files:
    path = Path(filename)

    with path.open("rb") as excel_file:
        records, errors = parse_attendance_excel(excel_file)

    print(f"\n{filename}")
    print("Attendance records:", len(records))
    print("Errors:", len(errors))
    print("Sample:", records[:3])

    for error in errors[:5]:
        print("ERROR:", error)
