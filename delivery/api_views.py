from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from django.shortcuts import get_object_or_404
from orders.models import Order
from .serializers import OrderSerializer, OrderStatusUpdateSerializer

class CustomAuthToken(ObtainAuthToken):
    """
    Login API: Drivers/Admins can login to the mobile app and get an auth token.
    """
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        # Only allow admin or users with specific roles to access the delivery app
        if not user.is_admin():
            return Response({'error': 'Only authorized delivery personnel can login.'}, status=403)
        token, created = Token.objects.get_or_create(user=user)
        return Response({
            'token': token.key,
            'user_id': user.pk,
            'username': user.username
        })

class DeliveryOrderListAPI(generics.ListAPIView):
    """
    Returns a list of orders that need to be delivered.
    """
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Drivers typically need to see statuses that require them to move items
        return Order.objects.filter(
            status__in=['confirmed', 'admin_picked', 'out_for_delivery']
        ).order_by('created_at')

class DeliveryOrderUpdateAPI(generics.UpdateAPIView):
    """
    Allows the delivery driver to update the status of the order.
    """
    serializer_class = OrderStatusUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Order.objects.all()

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        serializer = self.get_serializer(order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        # Additional logic when order is marked as delivered
        if serializer.validated_data.get('status') == 'delivered':
            from django.utils import timezone
            from .models import Delivery
            delivery, _ = Delivery.objects.get_or_create(order=order)
            delivery.delivered_at = timezone.now()
            delivery.save()
            
        return Response(OrderSerializer(order).data)
