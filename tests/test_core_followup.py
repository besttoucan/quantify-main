"""Quantitative regressions from the remaining backend-core audit findings."""
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from quantify_app import intelligence, item_analysis, transactions
from quantify_app.database import connect, initialize


class CoreFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "core.db"
        initialize(self.path)
        with connect(self.path) as conn:
            conn.execute("INSERT INTO organizations(id,name,created_at) VALUES('org','Test','2026-01-01')")
            conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
                latitude,longitude,timezone,open_hour,close_hour)
                VALUES('loc','org','Test','Cafe','','Denver','CO','',0,0,'America/Denver',7,22)""")

    def tearDown(self):
        self.temp.cleanup()

    def test_historical_item_analysis_cannot_see_later_sales(self):
        target = date(2026, 8, 1)
        with connect(self.path) as conn:
            for item in ['first','other']:
                conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES(?,'loc',?,'Lunch',10)",(item,item))
                for n in range(1,121):
                    quantity = (70 if item == 'first' else 50) + n % 7 + (n % 5) * 3
                    day = (target - timedelta(days=n)).isoformat()
                    conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc',?,?,?,?)",(item,day,quantity,quantity*10))
                    conn.execute("INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue) VALUES('loc',?,?,9,?,?)",(item,day,quantity,quantity*10))
            before = item_analysis.item_profile(conn,'loc','first',target)
            # Later sales would reverse the rank, peak hour and correlation if leaked.
            for n in range(0,25):
                day = (target + timedelta(days=n)).isoformat()
                for item,quantity in [('first',1),('other',5000+n*100)]:
                    conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc',?,?,?,?)",(item,day,quantity,quantity*10))
                    conn.execute("INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue) VALUES('loc',?,?,21,?,?)",(item,day,quantity*10000,quantity*100000))
                conn.execute("""INSERT INTO day_accuracy(location_id,date,predicted_units,actual_units,predicted_sales,
                    actual_sales,accuracy,items_json,scored_at) VALUES('loc',?,1,100,10,1000,1,?,'2026-09-01')""",
                    (day,json.dumps([{'item_id':'first','predicted':1,'actual':100}])))
            after = item_analysis.item_profile(conn,'loc','first',target)
            for key in ['standing','hourly','accuracy','related','last_sale_date']:
                with self.subTest(section=key):
                    self.assertEqual(before[key],after[key])
            self.assertEqual(after['standing']['revenue_rank'],1)
            self.assertEqual(after['hourly']['busiest'],'9 AM')
            self.assertEqual(after['last_sale_date'],(target-timedelta(days=1)).isoformat())

    def test_history_scores_retired_items_and_zero_sales_opening_calls(self):
        target = date(2026, 8, 1)
        with connect(self.path) as conn:
            for item,active,expected,actual in [('active',1,20,20),('retired',0,100,80),('unsold',1,10,None)]:
                conn.execute("INSERT INTO menu_items(id,location_id,name,category,price,active) VALUES(?,'loc',?,'Lunch',10,?)",(item,item,active))
                if actual is not None:
                    conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc',?,?,?,?)",(item,target.isoformat(),actual,actual*10))
                conn.execute("""INSERT INTO forecast_calls(location_id,date,item_id,expected,lower,upper,price,model_version,locked_at,locked_local)
                    VALUES('loc',?,?,?,0,200,10,'test','2026-08-01T05:00:00Z','05:00')""",(target.isoformat(),item,expected))
            missing = target + timedelta(days=1)
            conn.execute("""INSERT INTO forecast_calls(location_id,date,item_id,expected,lower,upper,price,model_version,locked_at,locked_local)
                VALUES('loc',?,'unsold',999,0,1000,10,'test','2026-08-02T05:00:00Z','05:00')""",(missing.isoformat(),))
            transactions.score_range(conn,'loc',target,missing)
            score = conn.execute("SELECT * FROM day_accuracy WHERE location_id='loc' AND date=?",(target.isoformat(),)).fetchone()
            entries = {row['item_id']:row for row in json.loads(score['items_json'])}
            self.assertEqual(set(entries),{'active','retired','unsold'})
            self.assertEqual(entries['unsold']['actual'],0)
            self.assertEqual((score['predicted_units'],score['actual_units'],score['accuracy']),(130,100,70))
            self.assertIsNone(conn.execute("SELECT date FROM day_accuracy WHERE location_id='loc' AND date=?",(missing.isoformat(),)).fetchone())

    def test_zero_fahrenheit_is_a_measurement_not_missing_weather(self):
        with connect(self.path) as conn:
            location = conn.execute("SELECT * FROM locations WHERE id='loc'").fetchone()
            context = intelligence.build_context(location,date(2026,1,20),
                {'temp_high':0,'temp_low':0,'condition':'Cold','source':'test-provider'},[])
            self.assertTrue(context['weather_available'])
            self.assertEqual((context['temp_high'],context['temp_low']),(0,0))


if __name__ == '__main__':
    unittest.main()
