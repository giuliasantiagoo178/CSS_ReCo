from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import DonationForm, MessageForm
from .models import Donation, Message


User = get_user_model()


def index(request):
    """Listar anúncios com filtros simples de busca, condição e cidade."""
    donations = Donation.objects.filter(is_available=True)
    search = (request.GET.get('q') or '').strip()
    condition = request.GET.get('condition') or ''
    city = (request.GET.get('city') or '').strip()
    order = request.GET.get('order') or 'recent'

    if search:
        donations = donations.filter(
            Q(title__icontains=search) | Q(description__icontains=search)
        )
    if condition:
        donations = donations.filter(condition=condition)
    if city:
        donations = donations.filter(city__icontains=city)

    if order == 'oldest':
        donations = donations.order_by('created_at')
    elif order == 'name':
        donations = donations.order_by('title')
    else:
        donations = donations.order_by('-created_at')

    city_choices = (
        Donation.objects.exclude(city='').exclude(city__isnull=True)
        .values_list('city', flat=True)
        .distinct()
        .order_by('city')
    )

    context = {
        'donations': donations.select_related('donor'),
        'filters': {
            'search': search,
            'condition': condition,
            'city': city,
            'order': order,
        },
        'city_choices': city_choices,
        'condition_choices': Donation.CONDITION_CHOICES,
    }
    return render(request, 'marketplace/index.html', context)


@login_required(login_url='usuario:login')
def create(request):
    """Criar um novo anúncio de doação."""
    if request.method == 'POST':
        form = DonationForm(request.POST, request.FILES)
        if form.is_valid():
            donation = form.save(commit=False)
            donation.donor = request.user
            donation.save()
            messages.success(request, 'Anúncio publicado com sucesso!')
            return redirect('doacoes:detail', donation.pk)
    else:
        form = DonationForm()

    return render(request, 'marketplace/create.html', {'form': form})


def detail(request, pk):
    donation = get_object_or_404(
        Donation.objects.select_related('donor'),
        pk=pk,
    )
    related = (
        Donation.objects.filter(is_available=True)
        .exclude(pk=pk)
        .order_by('-created_at')[:3]
    )
    return render(
        request,
        'marketplace/detail.html',
        {'donation': donation, 'related': related},
    )


@login_required(login_url='usuario:login')
def chats(request):
    """Resumo das conversas do usuário (como doador ou interessado)."""
    user = request.user
    latest = (
        Message.objects.filter(Q(sender=user) | Q(recipient=user))
        .select_related('donation', 'donation__donor', 'sender', 'recipient')
        .order_by('-created_at')
    )

    conversations = {}
    for msg in latest:
        other = msg.recipient if msg.sender == user else msg.sender
        if other is None:
            continue
        key = (msg.donation_id, other.id)
        stored = conversations.get(key)
        if not stored or msg.created_at > stored['last_message'].created_at:
            conversations[key] = {
                'donation': msg.donation,
                'other': other,
                'last_message': msg,
            }

    ordered = sorted(
        conversations.values(),
        key=lambda item: item['last_message'].created_at,
        reverse=True,
    )

    return render(request, 'marketplace/chats.html', {'conversations': ordered})


def _participants_for_donation(donation):
    """Retorna usuários (exceto o doador) que já trocaram mensagens nesse anúncio."""
    user_ids = set(
        donation.messages.values_list('sender_id', flat=True)
    ) | set(
        donation.messages.values_list('recipient_id', flat=True)
    )
    user_ids.discard(donation.donor_id)
    if not user_ids:
        return User.objects.none()
    return User.objects.filter(id__in=user_ids)


def _resolve_partner(request, donation, participant_id):
    """Determina o interlocutor da conversa conforme o usuário logado."""
    if request.user == donation.donor:
        if not participant_id:
            return None, JsonResponse(
                {'error': 'Selecione um interessado para continuar o chat.'},
                status=400,
            )
        try:
            participant = User.objects.get(pk=participant_id)
        except User.DoesNotExist:
            return None, JsonResponse({'error': 'Usuário não encontrado.'}, status=404)
        if participant == request.user:
            return None, JsonResponse({'error': 'Conversa inválida.'}, status=400)
        valid_ids = set(_participants_for_donation(donation).values_list('id', flat=True))
        if participant.id not in valid_ids:
            return None, JsonResponse(
                {'error': 'Esse interessado ainda não iniciou conversa.'},
                status=400,
            )
        return participant, None

    # Interessado falando com o doador
    if participant_id and str(participant_id) != str(donation.donor_id):
        return None, JsonResponse({'error': 'Conversa inválida.'}, status=400)
    return donation.donor, None


@login_required(login_url='usuario:login')
def chat(request, pk):
    donation = get_object_or_404(Donation.objects.select_related('donor'), pk=pk)
    is_owner = donation.donor == request.user
    participants = list(_participants_for_donation(donation)) if is_owner else []

    active_participant = None
    participant_param = request.GET.get('with')

    if is_owner:
        if participant_param:
            active_participant = next(
                (p for p in participants if str(p.pk) == participant_param),
                None,
            )
        if not active_participant and participants:
            active_participant = participants[0]
    else:
        active_participant = donation.donor

    messages_url = reverse('doacoes:messages_json', kwargs={'pk': donation.pk})
    post_url = reverse('doacoes:post_message', kwargs={'pk': donation.pk})

    if active_participant:
        messages_url = f"{messages_url}?participant={active_participant.pk}"
        post_url = f"{post_url}?participant={active_participant.pk}"

    context = {
        'donation': donation,
        'form': MessageForm(),
        'is_owner': is_owner,
        'participants': participants,
        'active_participant': active_participant,
        'messages_url': messages_url if active_participant else None,
        'post_url': post_url if active_participant else None,
    }

    if is_owner and not active_participant:
        messages.info(
            request,
            'Nenhum interessado iniciou conversa ainda. Volte quando receber mensagens.',
        )

    return render(request, 'marketplace/chat.html', context)


@login_required(login_url='usuario:login')
def messages_json(request, pk):
    donation = get_object_or_404(Donation, pk=pk)
    participant_id = request.GET.get('participant')
    other, error_response = _resolve_partner(request, donation, participant_id)
    if error_response:
        return error_response

    qs = (
        Message.objects.filter(donation=donation)
        .filter(
            Q(sender=request.user, recipient=other)
            | Q(sender=other, recipient=request.user)
        )
        .order_by('created_at')
        .select_related('sender')
    )

    data = [
        {
            'id': msg.id,
            'sender': msg.sender.get_full_name() or msg.sender.get_username(),
            'text': msg.text,
            'created_at': msg.created_at.isoformat(),
        }
        for msg in qs
    ]
    return JsonResponse({'messages': data})


@login_required(login_url='usuario:login')
def post_message(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido.'}, status=405)

    donation = get_object_or_404(Donation, pk=pk)
    participant_id = request.GET.get('participant')
    other, error_response = _resolve_partner(request, donation, participant_id)
    if error_response:
        return error_response

    form = MessageForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': 'Mensagem inválida.'}, status=400)

    message = form.save(commit=False)
    message.donation = donation
    message.sender = request.user
    message.recipient = other
    message.save()

    return JsonResponse({'ok': True})
