from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Group, GroupMember, Expense, ExpenseParticipant
import io
import csv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registered successfully!')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    groups = [gm.group for gm in current_user.groups]
    return render_template('dashboard.html', groups=groups)

@app.route('/create_group', methods=['POST'])
@login_required
def create_group():
    name = request.form['name']
    group = Group(name=name)
    db.session.add(group)
    db.session.commit()
    member = GroupMember(user_id=current_user.id, group_id=group.id)
    db.session.add(member)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/group/<int:group_id>')
@login_required
def group(group_id):
    group = Group.query.get_or_404(group_id)
    if not any(m.user_id == current_user.id for m in group.members):
        flash('Access denied')
        return redirect(url_for('dashboard'))
    balances = {m.user.username: m.balance for m in group.members}
    expenses = group.expenses
    return render_template('group.html', group=group, balances=balances, expenses=expenses)

@app.route('/add_member/<int:group_id>', methods=['POST'])
@login_required
def add_member(group_id):
    username = request.form['username']
    user = User.query.filter_by(username=username).first()
    if user:
        member = GroupMember(user_id=user.id, group_id=group_id)
        db.session.add(member)
        db.session.commit()
        flash('Member added')
    else:
        flash('User not found')
    return redirect(url_for('group', group_id=group_id))

@app.route('/add_expense/<int:group_id>', methods=['GET', 'POST'])
@login_required
def add_expense(group_id):
    group = Group.query.get_or_404(group_id)
    if request.method == 'POST':
        description = request.form['description']
        amount = float(request.form['amount'])
        currency = request.form['currency']
        participant_ids = request.form.getlist('participants')
        num_parts = len(participant_ids)
        share = amount / num_parts if num_parts > 0 else 0

        expense = Expense(description=description, amount=amount, payer_id=current_user.id, group_id=group_id, currency=currency)
        db.session.add(expense)
        db.session.commit()

        # Update payer balance positively
        payer_member = GroupMember.query.filter_by(user_id=current_user.id, group_id=group_id).first()
        payer_member.balance += amount
        db.session.commit()

        for pid in participant_ids:
            part = ExpenseParticipant(expense_id=expense.id, user_id=int(pid), share=share)
            db.session.add(part)
            member = GroupMember.query.filter_by(user_id=int(pid), group_id=group_id).first()
            member.balance -= share
            db.session.commit()

        flash('Expense added')
        return redirect(url_for('group', group_id=group_id))
    members = group.members
    return render_template('add_expense.html', group=group, members=members)

@app.route('/settlement/<int:group_id>')
@login_required
def settlement(group_id):
    group = Group.query.get_or_404(group_id)
    balances = sorted([(m.user.username, m.balance) for m in group.members], key=lambda x: x[1])
    settlements = []
    i, j = 0, len(balances) - 1
    while i < j:
        debtor, debt = balances[i]
        creditor, credit = balances[j]
        if abs(debt) < credit:
            settlements.append(f"{debtor} pays {creditor} {abs(debt):.2f}")
            balances[j] = (creditor, credit + debt)
            i += 1
        elif abs(debt) > credit:
            settlements.append(f"{debtor} pays {creditor} {credit:.2f}")
            balances[i] = (debtor, debt + credit)
            j -= 1
        else:
            settlements.append(f"{debtor} pays {creditor} {abs(debt):.2f}")
            i += 1
            j -= 1
    return render_template('settlement.html', settlements=settlements, group=group)

@app.route('/export_csv/<int:group_id>')
@login_required
def export_csv(group_id):
    group = Group.query.get_or_404(group_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['User', 'Balance'])
    for m in group.members:
        writer.writerow([m.user.username, m.balance])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name=f"{group.name}_balances.csv")

@app.route('/share_link/<int:group_id>')
@login_required
def share_link(group_id):
    # In production, use full URL; here it's relative
    link = url_for('group', group_id=group_id, _external=True)
    flash(f'Copyable link: {link}')
    return redirect(url_for('group', group_id=group_id))

@app.route('/convert_currency/<int:group_id>', methods=['GET', 'POST'])
@login_required
def convert_currency(group_id):
    group = Group.query.get_or_404(group_id)
    if request.method == 'POST':
        target_currency = request.form['target_currency']
        exchange_rates = {}  # e.g., {'USD': 1.0, 'EUR': float(request.form['EUR_rate'])}
        for currency in set(e.currency for e in group.expenses):
            if currency != target_currency:
                rate_key = f'{currency}_rate'
                if rate_key in request.form:
                    exchange_rates[currency] = float(request.form[rate_key])
                else:
                    exchange_rates[currency] = 1.0  # Default if not provided

        converted_balances = {}
        for member in group.members:
            converted_balance = 0.0
            # Recalculate balances with conversion
            for expense in group.expenses:
                if any(p.user_id == member.user_id for p in expense.participants):
                    share = next(p.share for p in expense.participants if p.user_id == member.user_id)
                    rate = exchange_rates.get(expense.currency, 1.0)
                    converted_balance -= share * rate
                if expense.payer_id == member.user_id:
                    rate = exchange_rates.get(expense.currency, 1.0)
                    converted_balance += expense.amount * rate
            converted_balances[member.user.username] = converted_balance / exchange_rates.get(target_currency, 1.0)  # Normalize if needed

        return render_template('convert_currency.html', group=group, balances=converted_balances, target_currency=target_currency)
    currencies = set(e.currency for e in group.expenses)
    return render_template('convert_currency.html', group=group, currencies=currencies)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
