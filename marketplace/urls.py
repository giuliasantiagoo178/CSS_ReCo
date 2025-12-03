from django.urls import path
from . import views

app_name = 'doacoes'

urlpatterns = [
    path('', views.index, name='index'),
    path('novo/', views.create, name='create'),
    path('chats/', views.chats, name='chats'),
    path('doacao/<int:pk>/', views.detail, name='detail'),
    path('doacao/<int:pk>/chat/', views.chat, name='chat'),
    path('doacao/<int:pk>/messages/', views.messages_json, name='messages_json'),
    path('doacao/<int:pk>/messages/send/', views.post_message, name='post_message'),
]
