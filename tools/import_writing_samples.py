"""Import the explicitly supplied product examples into the ignored local library."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastnews.writing_samples import import_samples

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--year',type=int,default=2026)
    args=parser.parse_args()
    print(json.dumps(import_samples(args.directory,year=args.year),ensure_ascii=False))
