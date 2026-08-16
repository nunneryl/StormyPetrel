echo "=== git ==="
git fetch -q origin
git log --oneline -1
echo "behind main by: $(git rev-list --count HEAD..origin/main) commit(s)"
echo ""
echo "=== uncommitted ==="
git status --short
echo ""
echo "=== refreshing NOAA rosters ==="
curl -s https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt -o pipeline/geodata/ndbc_latest_obs.txt
curl -s https://www.ndbc.noaa.gov/activestations.xml -o pipeline/geodata/ndbc_stations.xml
ls -l pipeline/geodata/ndbc_latest_obs.txt | awk '{print $6, $7, $8}'
echo ""
echo "=== tiers ==="
python3 -c "import json,collections; print(collections.Counter(x.get('swell_window_source') for x in json.load(open('pipeline/spots_enriched.json'))))"
