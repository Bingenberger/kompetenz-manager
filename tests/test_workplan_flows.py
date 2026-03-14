import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Foerderinhalt,
    Foerderplan,
    Item,
    Schueler,
    User,
    UserKlassenzuordnung,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskCompetency,
    WorkPlanTaskEvaluation,
)


CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class WorkPlanFlowsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_wp_test_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.upload_dir = os.path.join(self.tmpdir, 'uploads')

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': f"sqlite:///{self.db_path}",
            'UPLOAD_FOLDER': self.upload_dir,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

            admin = User(username='admin', password_hash=generate_password_hash('adminpass'))
            teacher_a = User(username='teacher_a', password_hash=generate_password_hash('pass'))
            teacher_b = User(username='teacher_b', password_hash=generate_password_hash('pass'))
            db.session.add_all([admin, teacher_a, teacher_b])
            db.session.flush()

            db.session.add_all([
                UserKlassenzuordnung(user_id=teacher_a.id, klasse='4a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=teacher_b.id, klasse='4b', rolle='klassenleitung'),
            ])

            self.student_a = Schueler(vorname='Max', nachname='A', klasse='4a')
            self.student_a2 = Schueler(vorname='Mia', nachname='A', klasse='4a')
            self.student_b = Schueler(vorname='Tom', nachname='B', klasse='4b')
            db.session.add_all([self.student_a, self.student_a2, self.student_b])
            db.session.flush()
            self.student_a_id = self.student_a.id
            self.student_a2_id = self.student_a2.id
            self.student_b_id = self.student_b.id

            bogen = Bogen(titel='Deutsch')
            db.session.add(bogen)
            db.session.flush()
            self.item_om = Item(bogen_id=bogen.id, text='Liest Wörter korrekt', bereich='Lesen')
            self.item_rep = Item(bogen_id=bogen.id, text='Schreibt Sätze selbstständig', bereich='Schreiben')
            db.session.add_all([self.item_om, self.item_rep])
            db.session.flush()
            self.item_om_id = self.item_om.id
            self.item_rep_id = self.item_rep.id

            now = datetime.utcnow()
            db.session.add(Beobachtung(
                schueler_id=self.student_a.id,
                item_id=self.item_om.id,
                wert=2,
                datum=now,
                kommentar='Unsicher.',
            ))

            fp = Foerderplan(
                schueler_id=self.student_a.id,
                creator_user_id=teacher_a.id,
                titel='FP aktiv',
                status='aktiv',
                datum_erstellung=now.date(),
            )
            db.session.add(fp)
            db.session.flush()
            db.session.add(Foerderinhalt(
                plan_id=fp.id,
                foerderziel='Leseverständnis verbessern',
                ist_zustand='Noch lückenhaft',
                soll_zustand='Sicheres Verständnis',
                massnahmen='Tägliches Lesen',
                status_id=0,
            ))

            wp_old = WorkPlan(
                student_id=self.student_a.id,
                created_by_user_id=teacher_a.id,
                period_start=(now - timedelta(days=14)).date(),
                period_end=(now - timedelta(days=7)).date(),
                status='evaluated',
            )
            db.session.add(wp_old)
            db.session.flush()
            task_old = WorkPlanTask(
                work_plan_id=wp_old.id,
                title='Alte Aufgabe',
                source_type='manual',
                sort_order=1,
            )
            db.session.add(task_old)
            db.session.flush()
            db.session.add(WorkPlanTaskCompetency(task_id=task_old.id, item_id=self.item_rep.id))
            db.session.add(WorkPlanTaskEvaluation(task_id=task_old.id, rating='bad', re_proposal=True))

            self.plan_a = WorkPlan(
                student_id=self.student_a.id,
                created_by_user_id=teacher_a.id,
                period_start=now.date(),
                period_end=(now + timedelta(days=7)).date(),
                status='draft',
            )
            self.plan_b = WorkPlan(
                student_id=self.student_b.id,
                created_by_user_id=teacher_b.id,
                period_start=now.date(),
                period_end=(now + timedelta(days=7)).date(),
                status='draft',
            )
            db.session.add_all([self.plan_a, self.plan_b])
            db.session.flush()
            self.task_a = WorkPlanTask(work_plan_id=self.plan_a.id, title='Quellaufgabe', source_type='manual', sort_order=1)
            db.session.add(self.task_a)
            db.session.flush()
            db.session.add(WorkPlanTaskCompetency(task_id=self.task_a.id, item_id=self.item_om.id))
            self.plan_a_id = self.plan_a.id
            self.task_a_id = self.task_a.id

            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_csrf(self, response):
        html = response.get_data(as_text=True)
        match = CSRF_RE.search(html)
        self.assertIsNotNone(match, 'CSRF-Token nicht gefunden')
        return match.group(1)

    def _login(self, username, password):
        login_page = self.client.get('/login')
        token = self._get_csrf(login_page)
        response = self.client.post(
            '/login',
            data={'username': username, 'password': password, '_csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def _csrf_token_for_post(self):
        page = self.client.get('/konto')
        self.assertEqual(page.status_code, 200)
        return self._get_csrf(page)

    def test_rbac_teacher_sees_only_own_workplans(self):
        self._login('teacher_a', 'pass')

        res = self.client.get('/api/work-plans?format=json')
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        creator_ids = {row['createdByUserId'] for row in payload['plans']}
        self.assertEqual(len(creator_ids), 1)

        with self.app.app_context():
            teacher_a = User.query.filter_by(username='teacher_a').first()
            self.assertEqual(creator_ids.pop(), teacher_a.id)

        self.client.get('/logout')
        self._login('teacher_b', 'pass')
        forbidden_view = self.client.get(f'/arbeitsplan/{self.plan_a_id}/bearbeiten')
        self.assertEqual(forbidden_view.status_code, 404)

    def test_copy_task_endpoint_copies_competency_links(self):
        self._login('teacher_a', 'pass')
        token = self._csrf_token_for_post()

        res = self.client.post(
            f'/api/work-plans/{self.plan_a_id}/tasks/{self.task_a_id}/copy?format=json',
            data={'_csrf_token': token, 'targetStudentId': str(self.student_a2_id)},
        )
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertIn('targetWorkPlanId', payload)

        with self.app.app_context():
            target_plan = db.session.get(WorkPlan, payload['targetWorkPlanId'])
            self.assertIsNotNone(target_plan)
            self.assertEqual(target_plan.student_id, self.student_a2_id)
            copied_task = WorkPlanTask.query.filter_by(work_plan_id=target_plan.id).first()
            self.assertIsNotNone(copied_task)
            comp_ids = {c.item_id for c in copied_task.competency_links}
            self.assertIn(self.item_om_id, comp_ids)

    def test_suggestions_include_o_minus_active_goal_and_reproposal(self):
        self._login('teacher_a', 'pass')

        res = self.client.get(f'/api/work-plan-suggestions?format=json&studentId={self.student_a_id}')
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()

        o_minus_ids = {row['item']['id'] for row in payload['oMinusCompetencies']}
        self.assertIn(self.item_om_id, o_minus_ids)

        goals = payload['activePlanGoals']
        self.assertTrue(any('Leseverständnis' in (g.get('ziel') or '') for g in goals))

        re_prop_ids = {row['id'] for row in payload['reProposalCompetencies']}
        self.assertIn(self.item_rep_id, re_prop_ids)

    def test_observation_from_evaluation_creates_log_entry(self):
        self._login('teacher_a', 'pass')
        token = self._csrf_token_for_post()

        with self.app.app_context():
            task = WorkPlanTask(
                work_plan_id=self.plan_a_id,
                title='Eval-Aufgabe',
                source_type='manual',
                sort_order=2,
            )
            db.session.add(task)
            db.session.flush()
            db.session.add(WorkPlanTaskCompetency(task_id=task.id, item_id=self.item_om_id))
            db.session.add(WorkPlanTaskEvaluation(task_id=task.id, rating='partial', re_proposal=True))
            db.session.commit()
            task_id = task.id

        res = self.client.post(
            '/api/observations/from-evaluation?format=json',
            data={
                '_csrf_token': token,
                'taskId': task_id,
                'competencyId': str(self.item_om_id),
                'rating': 'partial',
                'note': 'Aus Evaluation',
                'date': datetime.utcnow().date().isoformat(),
            },
        )
        self.assertEqual(res.status_code, 200)

        with self.app.app_context():
            obs = (
                Beobachtung.query
                .filter_by(schueler_id=self.student_a_id, item_id=self.item_om_id)
                .order_by(Beobachtung.id.desc())
                .first()
            )
            self.assertIsNotNone(obs)
            self.assertEqual(obs.wert, 2)
            self.assertEqual(obs.anlass, 'Arbeitsplan-Evaluation')

    def test_class_pdf_export_respects_date_filter(self):
        self._login('teacher_a', 'pass')

        with self.app.app_context():
            now = datetime.utcnow().date()
            extra_plan = WorkPlan(
                student_id=self.student_a2_id,
                created_by_user_id=User.query.filter_by(username='teacher_a').first().id,
                period_start=now - timedelta(days=90),
                period_end=now - timedelta(days=83),
                status='in_planung',
            )
            db.session.add(extra_plan)
            db.session.commit()

            in_range_start = (now - timedelta(days=1)).isoformat()
            in_range_end = (now + timedelta(days=10)).isoformat()

        with patch('routes.workplan_routes.convert_odt_bytes_to_pdf', return_value=b'%PDF-1.4\n%mock\n'):
            res = self.client.get(
                f'/arbeitsplaene/klasse/export/pdf?tab=own&klasse=4a&klasse_von={in_range_start}&klasse_bis={in_range_end}'
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'application/pdf')

    def test_workplan_next_context_is_preserved_across_list_view_edit_and_evaluation(self):
        self._login('teacher_a', 'pass')

        source_url = f'/arbeitsplaene?tab=own&schueler_id={self.student_a_id}'
        list_page = self.client.get(source_url)
        self.assertEqual(list_page.status_code, 200)
        list_html = list_page.get_data(as_text=True)
        expected_next_param = 'next=/arbeitsplaene?tab%3Down'
        self.assertIn(expected_next_param, list_html)

        child_view = self.client.get(
            f'/arbeitsplan/{self.plan_a_id}/kind?next=/arbeitsplaene?tab=own&schueler_id={self.student_a_id}'
        )
        self.assertEqual(child_view.status_code, 200)
        child_html = child_view.get_data(as_text=True)
        self.assertIn('/arbeitsplaene?tab=own', child_html)

        evaluate_view = self.client.get(
            f'/arbeitsplan/{self.plan_a_id}/evaluate?next=/arbeitsplaene?tab=own&schueler_id={self.student_a_id}'
        )
        self.assertEqual(evaluate_view.status_code, 200)
        evaluate_html = evaluate_view.get_data(as_text=True)
        self.assertIn('/arbeitsplaene?tab=own', evaluate_html)

    def test_delete_workplan_from_page(self):
        self._login('teacher_a', 'pass')
        token = self._csrf_token_for_post()

        res = self.client.post(
            f'/arbeitsplan/{self.plan_a_id}/delete',
            data={'_csrf_token': token, 'tab': 'own', 'view': 'student'},
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 302)

        with self.app.app_context():
            deleted = db.session.get(WorkPlan, self.plan_a_id)
            self.assertIsNone(deleted)


if __name__ == '__main__':
    unittest.main()
