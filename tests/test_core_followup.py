"""Quantitative regressions from the remaining backend-core audit findings."""
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from quantify_app import explain, intelligence, intraday, item_analysis, transactions
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

    def test_first_week_needs_a_recorded_call_to_be_scored(self):
        target = date(2026, 8, 1)
        with connect(self.path) as conn:
            conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES('new','loc','New sandwich','Lunch',10)")
            for n in range(7):
                conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc','new',?,50,500)",
                    ((target-timedelta(days=n)).isoformat(),))
            item = conn.execute("SELECT * FROM menu_items WHERE id='new'").fetchone()
            forecast = intelligence.forecast_item(conn,item,target)
            self.assertTrue(forecast['new_item'])
            self.assertEqual(forecast['expected'],0)
            transactions.score_range(conn,'loc',target,target)
            score = transactions.ensure_day_scored(conn,'loc',target)
            self.assertEqual(score['actual_units'],50)
            self.assertIsNone(score['predicted_units'])
            self.assertIsNone(score['accuracy'])
            self.assertFalse(score['score_complete'])
            self.assertEqual(json.loads(score['items_json'])[0]['forecast_reason'],'not_enough_history')
            self.assertTrue(all(hour['predicted'] is None for hour in json.loads(score['hourly_json'])))
            profile = item_analysis.item_profile(conn,'loc','new',target)
            self.assertIsNone(profile['today']['expected'])
            self.assertEqual(profile['today']['forecast_reason'],'not_enough_history')
            conn.execute("""INSERT INTO forecast_calls(location_id,date,item_id,expected,lower,upper,price,model_version,locked_at,locked_local)
                VALUES('loc',?,'new',46,30,60,10,'test','2026-08-01T05:00:00Z','05:00')""",(target.isoformat(),))
            transactions.score_range(conn,'loc',target,target)
            score = conn.execute("SELECT * FROM day_accuracy WHERE date=?",(target.isoformat(),)).fetchone()
            self.assertEqual((score['predicted_units'],score['actual_units'],score['accuracy']),(46,50,92))
            self.assertEqual(score['call_source'],'stored')

    def test_legacy_read_repair_restores_calls_and_keeps_saved_row_unchanged(self):
        target = date(2026, 8, 1)
        with connect(self.path) as conn:
            for item,active,expected,actual in [('active',1,20,20),('retired',0,100,80),('unsold',1,10,None)]:
                conn.execute("INSERT INTO menu_items(id,location_id,name,category,price,active) VALUES(?,'loc',?,'Lunch',999,?)",(item,item,active))
                if actual is not None:
                    conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc',?,?,?,?)",(item,target.isoformat(),actual,actual*10))
                conn.execute("""INSERT INTO forecast_calls(location_id,date,item_id,expected,lower,upper,price,model_version,locked_at,locked_local)
                    VALUES('loc',?,?,?,0,200,10,'test','2026-08-01T05:00:00Z','05:00')""",(target.isoformat(),item,expected))
            entries = [{'item_id':'active','name':'active','predicted':20,'actual':20,'gap':0}]
            conn.execute("""INSERT INTO day_accuracy(location_id,date,predicted_units,actual_units,predicted_sales,actual_sales,
                accuracy,items_json,hourly_json,scored_at) VALUES('loc',?,20,20,200,200,100,?,?,'saved-original')""",
                (target.isoformat(),json.dumps(entries),json.dumps([{'hour':9,'predicted':20,'actual':100}])))
            raw = dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(target.isoformat(),)).fetchone())
            score = transactions.ensure_day_scored(conn,'loc',target)
            self.assertEqual((score['predicted_units'],score['actual_units'],score['accuracy']),(130,100,70))
            self.assertEqual(score['predicted_sales'],1300)  # Frozen call prices, never current 999.
            self.assertTrue(score['score_complete'])
            self.assertFalse(score['hourly_expected_available'])
            self.assertEqual(score['scored_item_count'],3)
            self.assertEqual(transactions.day_detail(conn,'loc',target)['predicted_units'],130)
            self.assertEqual(transactions.accuracy_trend(conn,'loc')['average'],70)
            self.assertEqual(item_analysis.item_accuracy(conn,'loc','retired',target+timedelta(days=1))['accuracy'],75)
            performance = intelligence.performance(conn,'loc',target+timedelta(days=1),7)
            self.assertEqual(performance['summary']['forecast_accuracy'],70)
            self.assertEqual(raw,dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(target.isoformat(),)).fetchone()))

    def test_legacy_partial_day_keeps_actuals_and_valid_reconstruction_without_false_accuracy(self):
        target = date(2026, 8, 1)
        with connect(self.path) as conn:
            for item,prior,actual in [('known',7,18),('cold',6,7),('missing',7,2)]:
                conn.execute("INSERT INTO menu_items(id,location_id,name,category,price,active) VALUES(?,'loc',?,'Lunch',999,?)",(item,item,0 if item=='known' else 1))
                for n in range(prior+1):
                    conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc',?,?,?,?)",
                        (item,(target-timedelta(days=n)).isoformat(),actual,actual*10))
            entries = [{'item_id':'known','name':'known','predicted':20,'actual':18,'gap':-2},
                       {'item_id':'cold','name':'cold','predicted':7,'actual':7,'gap':0}]
            conn.execute("""INSERT INTO day_accuracy(location_id,date,predicted_units,actual_units,predicted_sales,actual_sales,
                accuracy,items_json,scored_at) VALUES('loc',?,27,25,270,250,92,?,'saved-original')""",(target.isoformat(),json.dumps(entries)))
            raw = dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(target.isoformat(),)).fetchone())
            detail = transactions.day_detail(conn,'loc',target)
            self.assertEqual((detail['units'],detail['sales'],detail['total_actual_units']),(27,270,27))
            self.assertEqual((detail['scored_item_count'],detail['unscored_item_count'],detail['scored_actual_units']),(1,2,18))
            self.assertIsNone(detail['accuracy'])
            self.assertIsNone(detail['predicted_units'])
            self.assertIsNone(detail['predicted_sales'])
            items = {item['item_id']:item for item in detail['item_scores']}
            self.assertEqual(items['known']['predicted'],20)
            self.assertEqual(items['cold']['forecast_reason'],'not_enough_history')
            self.assertEqual(items['missing']['forecast_reason'],'missing_historical_expectation')
            self.assertIsNone(items['cold']['predicted'])
            self.assertIsNone(items['missing']['predicted'])
            listing = transactions.day_list(conn,'loc',before=target+timedelta(days=1),limit=1)['days'][0]
            self.assertEqual(listing['units'],27)
            self.assertFalse(listing['scored'])
            self.assertIsNone(listing['accuracy'])
            self.assertEqual(transactions.accuracy_trend(conn,'loc')['days'],0)
            self.assertEqual(item_analysis.item_accuracy(conn,'loc','cold',target+timedelta(days=1)),{'days':0})
            self.assertEqual(item_analysis.item_accuracy(conn,'loc','known',target+timedelta(days=1))['days'],1)
            self.assertEqual(intraday._location_sigma(conn,'loc'),0.16)
            review = explain.local_day_review(transactions.review_payload(detail))
            self.assertIn('Sold 27 items.',review['headline'])
            self.assertNotIn('0 expected',review['headline'])
            self.assertNotIn('Everything else',review['where_error_sat'])
            self.assertEqual(review['matters'],'')
            performance = intelligence.performance(conn,'loc',target+timedelta(days=1),7)
            self.assertEqual(performance['summary']['days_evaluated'],0)
            self.assertIsNone(performance['summary']['forecast_accuracy'])
            day = next(row for row in performance['daily'] if row['date']==target.isoformat())
            self.assertEqual(day['actual'],27)
            self.assertIsNone(day['predicted'])
            self.assertEqual(raw,dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(target.isoformat(),)).fetchone()))

    def test_reconstruction_starts_at_seven_prior_days(self):
        first = date(2026, 8, 1)
        with connect(self.path) as conn:
            conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES('new','loc','New sandwich','Lunch',10)")
            for n in range(9):
                conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc','new',?,50,500)",((first+timedelta(days=n)).isoformat(),))
            transactions.score_range(conn,'loc',first,first+timedelta(days=8))
            for n in range(9):
                score = transactions.ensure_day_scored(conn,'loc',first+timedelta(days=n))
                self.assertEqual(score['score_complete'],n>=7)
                self.assertEqual(score['actual_units'],50)
                self.assertEqual(score['predicted_units'],50 if n>=7 else None)

    def test_scoring_missing_neighbors_never_rewrites_a_saved_middle_day(self):
        first = date(2026,8,1)
        middle = first+timedelta(days=1)
        with connect(self.path) as conn:
            conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES('item','loc','Sandwich','Lunch',999)")
            for n in range(-8,3):
                conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc','item',?,50,500)",((first+timedelta(days=n)).isoformat(),))
            saved = [{'item_id':'item','name':'Sandwich','predicted':46,'actual':50,'gap':4}]
            conn.execute("""INSERT INTO day_accuracy(location_id,date,predicted_units,actual_units,predicted_sales,actual_sales,
                accuracy,items_json,scored_at) VALUES('loc',?,46,50,460,500,92,?,'original')""",(middle.isoformat(),json.dumps(saved)))
            before = dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(middle.isoformat(),)).fetchone())
            for _location,start,end in transactions.iter_scoring_targets(conn,chunk=30):
                self.assertFalse(start <= middle <= end)
            result = intelligence.performance(conn,'loc',first+timedelta(days=3),7)
            day = next(day for day in result['daily'] if day['date']==middle.isoformat())
            self.assertEqual(day['predicted'],46)
            after = dict(conn.execute("SELECT * FROM day_accuracy WHERE date=?",(middle.isoformat(),)).fetchone())
            self.assertEqual(before,after)


if __name__ == '__main__':
    unittest.main()
