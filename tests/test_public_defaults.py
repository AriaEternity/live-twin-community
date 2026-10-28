"""在独立子进程/临时数据库验证公开默认值，不接触开发实例。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOT = '''
from app import app
from models import db, User
app.config['TESTING'] = True
client = app.test_client()
with app.app_context():
    user = User(username='example_owner', email='owner@example.invalid')
    user.set_password('SyntheticPublic2026!')
    db.session.add(user)
    db.session.commit()
'''


class PublicDefaults(unittest.TestCase):
    def execute(self, source, production=False):
        with tempfile.TemporaryDirectory(prefix='public-defaults-') as directory:
            base = Path(directory)
            env = os.environ.copy()
            env.update(FLASK_DEBUG='0' if production else '1',
                       DATABASE_URL='sqlite:///'+(base/'test.sqlite').as_posix(),
                       UPLOAD_FOLDER=str(base/'uploads'),
                       SENSITIVE_WORDS_FILE=str(base/'synthetic-words.txt'),
                       PYTHONDONTWRITEBYTECODE='1')
            env.pop('SECRET_KEY',None)
            env.pop('TRUST_PROXY_HEADERS',None)
            run = subprocess.run([sys.executable,'-B','-X','utf8','-c',source],cwd=ROOT,
                                 env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
            return run

    def check_script(self, script):
        result = self.execute(BOOT+script)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_registration_never_grants_admin_even_for_configured_name(self):
        self.check_script('''
app.config['ADMIN_USERNAME']='public_signup'
client.get('/auth/register')
with client.session_transaction() as session:
    token=session['_csrf_token']
result=client.post('/auth/register',data={'username':'public_signup','email':'signup@example.invalid','password':'SyntheticPublic2026!','confirm':'SyntheticPublic2026!','_csrf_token':token})
assert result.status_code==302,result.status_code
with app.app_context():
    assert not User.query.filter_by(username='public_signup').one().is_admin
''')

    def test_cli_grants_only_existing_named_account(self):
        self.check_script('''
runner=app.test_cli_runner()
missing=runner.invoke(args=['grant-admin','missing_account'])
assert missing.exit_code!=0
result=runner.invoke(args=['grant-admin','example_owner'])
assert result.exit_code==0,result.output
with app.app_context():
    assert User.query.filter_by(username='example_owner').one().is_admin
''')

    def test_reset_password_is_interactive_and_not_printed(self):
        self.check_script('''
new_password='AnotherSynthetic2026!'
result=app.test_cli_runner().invoke(args=['reset-password','example_owner'],input=new_password+'\\n'+new_password+'\\n')
assert result.exit_code==0,result.output
assert new_password not in result.output
with app.app_context():
    assert User.query.filter_by(username='example_owner').one().check_password(new_password)
''')

    def test_untrusted_forwarded_host_is_ignored_by_default(self):
        self.check_script('''
result=client.get('/cards',headers={'X-Forwarded-Host':'untrusted.example.invalid','X-Forwarded-Proto':'https'})
assert result.status_code==200
assert 'untrusted.example.invalid' not in result.get_data(as_text=True)
''')

    def test_production_without_secret_refuses_startup(self):
        result=self.execute('import config',production=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('SECRET_KEY',result.stderr)


if __name__=='__main__':
    unittest.main()
