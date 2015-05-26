import datetime

from config import settings
from catwatch.lib.util_sqlalchemy import ResourceMixin
from catwatch.extensions import db
from catwatch.blueprints.billing.services import StripeSubscription


class Money(object):
    @classmethod
    def cents_to_dollars(cls, cents):
        """
        Convert cents to dollars.

        :param cents: Amount in cents
        :type cents: int
        :return: float
        """
        return round(cents / 100.0, 2)

    @classmethod
    def dollars_to_cents(cls, dollars):
        """
        Convert dollars to cents.

        :param dollars: Amount in dollars
        :type dollars: float
        :return: int
        """
        return int(dollars * 100)


class CreditCard(ResourceMixin, db.Model):
    IS_EXPIRING_THRESHOLD_MONTHS = 2

    __tablename__ = 'credit_cards'
    id = db.Column(db.Integer, primary_key=True)

    # Relationships.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id',
                                                  onupdate='CASCADE',
                                                  ondelete='CASCADE'),
                        index=True, nullable=False)

    # Card details.
    brand = db.Column(db.String(32))
    last4 = db.Column(db.Integer)
    exp_date = db.Column(db.Date, index=True)
    is_expiring = db.Column(db.Boolean(), nullable=False, server_default='0')

    def __init__(self, **kwargs):
        # Call Flask-SQLAlchemy's constructor.
        super(CreditCard, self).__init__(**kwargs)

    @classmethod
    def is_expiring_soon(cls, exp_date):
        """
        Determine whether or not this credit card is expiring soon.

        :param exp_date: Expiration date
        :type exp_date: date
        :return: bool
        """
        today = datetime.date.today()
        delta = CreditCard.IS_EXPIRING_THRESHOLD_MONTHS * 365 / 12
        today_with_delta = today + datetime.timedelta(delta)

        return exp_date <= today_with_delta

    @classmethod
    def extract_card_params(cls, stripe_customer):
        """
        Extract the credit card info from a stripe customer object.

        :param stripe_customer: Stripe customer
        :type stripe_customer: Stripe customer
        :return: Credit card dict
        """
        card_data = stripe_customer.cards.data[0]
        exp_date = datetime.date(card_data.exp_year, card_data.exp_month, 1)

        card = {
            'brand': card_data.brand,
            'last4': card_data.last4,
            'exp_date': exp_date,
            'is_expiring': CreditCard.is_expiring_soon(exp_date)
        }

        return card


class Subscription(ResourceMixin, db.Model):
    __tablename__ = 'subscriptions'
    id = db.Column(db.Integer, primary_key=True)

    # Relationships.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id',
                                                  onupdate='CASCADE',
                                                  ondelete='CASCADE'),
                        index=True, nullable=False)

    # Subscription details.
    plan = db.Column(db.String(128))

    def __init__(self, **kwargs):
        """
        It expects the following call signature:
          params = {
            'user': User object (ie. current_user),
            'name': 'Mr or Mrs. Foo',
            'plan': 'gold',
            'source': 'the_stripe_token'
          }
          subscription = Subscription(**params)
          subscription.begin_membership()

        :param user: Subscriber's user account
        :type user: User
        :param name: Subscriber's full name
        :type name: str
        :param plan: Subscriber's plan
        :type plan: str
        :param source: Stripe token
        :type source: str
        :return: Subscription
        """
        self.params = kwargs

        self.user_id = kwargs['user'].id
        self.plan = kwargs['plan']

        super(Subscription, self).__init__(user_id=self.user_id,
                                           plan=self.plan)

    @classmethod
    def get_plan_by_stripe_id(cls, id):
        """
        Pick the plan based on the Stripe ID.

        :param id: Stripe ID
        :type id: str
        :return: Dict of the plan or None
        """
        for key, value in settings.STRIPE_PLANS.iteritems():
            if value['id'] == id:
                return settings.STRIPE_PLANS[key]

        return None

    def begin_membership(self):
        """
        Return whether or not the membership was created successfully.

        :return: bool
        """
        user = self.params['user']

        # Create the customer on Stripe's end.
        stripe_params = {
            'source': self.params['stripe_token'],
            'email': user.email,
            'plan': self.plan
        }
        customer = StripeSubscription.create(stripe_params)

        # Update the user account.
        user.stripe_customer_id = customer.id
        user.name = self.params['name']

        # Create the credit card.
        credit_card = CreditCard(user_id=user.id,
                                 **CreditCard.extract_card_params(customer))

        db.session.add(user)
        db.session.add(credit_card)
        db.session.add(self)

        db.session.commit()

        return True
